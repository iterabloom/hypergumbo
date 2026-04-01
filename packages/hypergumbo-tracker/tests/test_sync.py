# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.sync — streamlined tracker PR workflow.

Covers all functions in sync.py: pure helpers, API helpers, preflight checks,
the full do_sync workflow, the CLI handler, and the setup wizard check.

Mocking strategy:
- ``hypergumbo_tracker.sync._git`` for all git subprocess calls.
- ``urllib.request.urlopen`` for all HTTP API calls.
- ``time.sleep`` to avoid delays.
- Gate files use real ``tmp_path`` directories (no mocking needed).
- ``.env`` loading uses real temp files.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from http.client import HTTPResponse
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from hypergumbo_tracker.sync import (
    PreflightResult,
    SyncResult,
    _FailoverState,
    _api_call,
    _detect_api_base,
    _detect_failover,
    _find_open_pr,
    _git,
    _load_env,
    _check_pr_merged,
    _close_pr,
    _log,
    _merge_pr,
    _poll_ci,
    _sum_added_lines,
    do_sync,
    pending_sync_lines,
    preflight_check,
)
from hypergumbo_tracker.sync_log import (
    RETENTION_DAYS,
    _LOG_FILENAME_RE,
    _parse_log_date,
    gc_old_logs,
    init_sync_log,
    write_log,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completed_process(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess for mocking _git."""
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _make_preflight(
    tmp_path: Path,
    changed_files: list[str] | None = None,
    **overrides: Any,
) -> PreflightResult:
    """Build a valid PreflightResult for do_sync tests."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir(exist_ok=True)
    defaults = {
        "ok": True,
        "repo_root": tmp_path,
        "git_dir": git_dir,
        "original_branch": "dev",
        "changed_files": changed_files or [".agent/tracker/.ops/.WI-test.ops"],
        "api_base": "https://codeberg.org/api/v1/repos/owner/repo",
        "forgejo_user": "testuser",
        "forgejo_token": "testtoken",
    }
    defaults.update(overrides)
    return PreflightResult(**defaults)


def _make_urlopen_response(
    body: dict[str, Any] | list[Any],
    status: int = 200,
) -> MagicMock:
    """Build a mock urlopen context manager response."""
    data = json.dumps(body).encode()
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = data
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# TestGit (real subprocess)
# ---------------------------------------------------------------------------


class TestGit:
    """Tests for _git — real subprocess wrapper (unmocked)."""

    def test_git_version(self, tmp_path: Path) -> None:
        """Verify _git runs a real command and captures output."""
        result = _git(tmp_path, "version", check=False)
        assert result.returncode == 0
        assert "git version" in result.stdout

    def test_git_failure(self, tmp_path: Path) -> None:
        """Verify check=False returns non-zero without raising."""
        result = _git(tmp_path, "log", "--oneline", "-1", check=False)
        # tmp_path is not a git repo, so this fails
        assert result.returncode != 0

    def test_git_with_env(self, tmp_path: Path) -> None:
        """Verify env parameter merges with os.environ."""
        result = _git(
            tmp_path, "version", check=False,
            env={"MY_TEST_VAR": "hello"},
        )
        assert result.returncode == 0
        assert "git version" in result.stdout


# ---------------------------------------------------------------------------
# TestLoadEnv
# ---------------------------------------------------------------------------


class TestLoadEnv:
    """Tests for _load_env — .env file parsing."""

    def test_missing_file(self, tmp_path: Path) -> None:
        result = _load_env(tmp_path)
        assert result == {}

    def test_simple_kv(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("FOO=bar\nBAZ=qux\n")
        result = _load_env(tmp_path)
        assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_quoted_values(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text(
            'SINGLE=\'hello\'\nDOUBLE="world"\n'
        )
        result = _load_env(tmp_path)
        assert result == {"SINGLE": "hello", "DOUBLE": "world"}

    def test_comments_and_blanks(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text(
            "# this is a comment\n\nKEY=value\n# another comment\n"
        )
        result = _load_env(tmp_path)
        assert result == {"KEY": "value"}

    def test_value_with_equals(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("URL=https://example.com?a=1&b=2\n")
        result = _load_env(tmp_path)
        assert result == {"URL": "https://example.com?a=1&b=2"}

    def test_no_equals_line_skipped(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("NOEQUALSSIGN\nGOOD=val\n")
        result = _load_env(tmp_path)
        assert result == {"GOOD": "val"}


# ---------------------------------------------------------------------------
# TestDetectApiBase
# ---------------------------------------------------------------------------


class TestDetectApiBase:
    """Tests for _detect_api_base — remote URL parsing."""

    @patch("hypergumbo_tracker.sync._git")
    def test_https_url(self, mock_git: MagicMock, tmp_path: Path) -> None:
        mock_git.return_value = _make_completed_process(
            stdout="https://codeberg.org/iterabloom/hypergumbo.git\n"
        )
        result = _detect_api_base(tmp_path)
        assert result == (
            "https://codeberg.org/api/v1/repos/iterabloom/hypergumbo"
        )

    @patch("hypergumbo_tracker.sync._git")
    def test_https_no_git_suffix(
        self, mock_git: MagicMock, tmp_path: Path
    ) -> None:
        mock_git.return_value = _make_completed_process(
            stdout="https://codeberg.org/owner/repo\n"
        )
        result = _detect_api_base(tmp_path)
        assert result == "https://codeberg.org/api/v1/repos/owner/repo"

    @patch("hypergumbo_tracker.sync._git")
    def test_ssh_url(self, mock_git: MagicMock, tmp_path: Path) -> None:
        mock_git.return_value = _make_completed_process(
            stdout="git@codeberg.org:owner/repo.git\n"
        )
        result = _detect_api_base(tmp_path)
        assert result == "https://codeberg.org/api/v1/repos/owner/repo"

    @patch("hypergumbo_tracker.sync._git")
    def test_custom_host(self, mock_git: MagicMock, tmp_path: Path) -> None:
        mock_git.return_value = _make_completed_process(
            stdout="https://git.example.com/myorg/myrepo.git\n"
        )
        result = _detect_api_base(tmp_path)
        assert result == (
            "https://git.example.com/api/v1/repos/myorg/myrepo"
        )

    @patch("hypergumbo_tracker.sync._git")
    def test_no_remote(self, mock_git: MagicMock, tmp_path: Path) -> None:
        mock_git.return_value = _make_completed_process(
            returncode=1, stderr="fatal: No such remote"
        )
        result = _detect_api_base(tmp_path)
        assert result == ""

    @patch("hypergumbo_tracker.sync._git")
    def test_https_embedded_credentials(
        self, mock_git: MagicMock, tmp_path: Path
    ) -> None:
        """URLs with embedded user:token@ are parsed correctly."""
        mock_git.return_value = _make_completed_process(
            stdout="https://agent:abc123token@codeberg.org/iterabloom/hypergumbo.git\n"
        )
        result = _detect_api_base(tmp_path)
        assert result == (
            "https://codeberg.org/api/v1/repos/iterabloom/hypergumbo"
        )

    @patch("hypergumbo_tracker.sync._git")
    def test_unparseable_url(
        self, mock_git: MagicMock, tmp_path: Path
    ) -> None:
        mock_git.return_value = _make_completed_process(
            stdout="file:///local/repo\n"
        )
        result = _detect_api_base(tmp_path)
        assert result == ""


# ---------------------------------------------------------------------------
# TestApiCall
# ---------------------------------------------------------------------------


class TestApiCall:
    """Tests for _api_call — urllib wrapper."""

    @patch("hypergumbo_tracker.sync.urlopen")
    def test_get_success(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _make_urlopen_response(
            {"state": "success"}, 200
        )
        status, body = _api_call(
            "GET", "https://api.example.com/test", "token123"
        )
        assert status == 200
        assert body == {"state": "success"}

    @patch("hypergumbo_tracker.sync.urlopen")
    def test_post_with_data(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _make_urlopen_response({"ok": True}, 200)
        status, body = _api_call(
            "POST",
            "https://api.example.com/test",
            "token123",
            data={"key": "value"},
        )
        assert status == 200
        assert body == {"ok": True}

        # Verify the request had data
        req = mock_urlopen.call_args[0][0]
        assert req.data is not None
        assert json.loads(req.data) == {"key": "value"}

    @patch("hypergumbo_tracker.sync.urlopen")
    def test_http_error(self, mock_urlopen: MagicMock) -> None:
        from urllib.error import HTTPError

        error = HTTPError(
            "https://api.example.com/test",
            422,
            "Unprocessable",
            {},
            BytesIO(json.dumps({"message": "bad"}).encode()),
        )
        mock_urlopen.side_effect = error
        status, body = _api_call(
            "GET", "https://api.example.com/test", "token123"
        )
        assert status == 422
        assert body == {"message": "bad"}

    @patch("hypergumbo_tracker.sync.urlopen")
    def test_http_error_no_body(self, mock_urlopen: MagicMock) -> None:
        from urllib.error import HTTPError

        error = HTTPError(
            "https://api.example.com/test",
            500,
            "Server Error",
            {},
            BytesIO(b""),
        )
        mock_urlopen.side_effect = error
        status, body = _api_call(
            "GET", "https://api.example.com/test", "token123"
        )
        assert status == 500
        assert body is None

    @patch("hypergumbo_tracker.sync.urlopen")
    def test_network_error(self, mock_urlopen: MagicMock) -> None:
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("Connection refused")
        status, body = _api_call(
            "GET", "https://api.example.com/test", "token123"
        )
        assert status == 0
        assert body is None

    @patch("hypergumbo_tracker.sync.urlopen")
    def test_timeout_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = TimeoutError()
        status, body = _api_call(
            "GET", "https://api.example.com/test", "token123"
        )
        assert status == 0
        assert body is None

    @patch("hypergumbo_tracker.sync.urlopen")
    def test_os_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = OSError("Network unreachable")
        status, body = _api_call(
            "GET", "https://api.example.com/test", "token123"
        )
        assert status == 0
        assert body is None

    @patch("hypergumbo_tracker.sync.urlopen")
    def test_http_error_unparseable_body(
        self, mock_urlopen: MagicMock
    ) -> None:
        from urllib.error import HTTPError

        error = HTTPError(
            "https://api.example.com/test",
            500,
            "Server Error",
            {},
            BytesIO(b"not json"),
        )
        mock_urlopen.side_effect = error
        status, body = _api_call(
            "GET", "https://api.example.com/test", "token123"
        )
        assert status == 500
        assert body is None

    @patch("hypergumbo_tracker.sync.urlopen")
    def test_empty_response_body(self, mock_urlopen: MagicMock) -> None:
        resp = MagicMock()
        resp.status = 204
        resp.read.return_value = b""
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp
        status, body = _api_call(
            "DELETE", "https://api.example.com/test", "token123"
        )
        assert status == 204
        assert body is None


# ---------------------------------------------------------------------------
# TestFindOpenPr
# ---------------------------------------------------------------------------


class TestFindOpenPr:
    """Tests for _find_open_pr — PR discovery by branch name."""

    @patch("hypergumbo_tracker.sync._api_call")
    def test_found(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (
            200,
            [
                {
                    "number": 42,
                    "head": {"ref": "tracker-sync/20260218", "sha": "abc123"},
                },
            ],
        )
        result = _find_open_pr(
            "https://api.example.com/repos/o/r",
            "token",
            "tracker-sync/20260218",
        )
        assert result == (42, "abc123")

    @patch("hypergumbo_tracker.sync._api_call")
    def test_found_by_label(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (
            200,
            [
                {
                    "number": 99,
                    "head": {
                        "ref": "other",
                        "label": "tracker-sync/20260218",
                        "sha": "def456",
                    },
                },
            ],
        )
        result = _find_open_pr(
            "https://api.example.com/repos/o/r",
            "token",
            "tracker-sync/20260218",
        )
        assert result == (99, "def456")

    @patch("hypergumbo_tracker.sync._api_call")
    def test_not_found(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (
            200,
            [
                {
                    "number": 1,
                    "head": {"ref": "other-branch", "sha": "xyz"},
                },
            ],
        )
        result = _find_open_pr(
            "https://api.example.com/repos/o/r",
            "token",
            "tracker-sync/20260218",
        )
        assert result is None

    @patch("hypergumbo_tracker.sync._api_call")
    def test_api_failure(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (0, None)
        result = _find_open_pr(
            "https://api.example.com/repos/o/r",
            "token",
            "tracker-sync/20260218",
        )
        assert result is None

    @patch("hypergumbo_tracker.sync._api_call")
    def test_empty_list(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (200, [])
        result = _find_open_pr(
            "https://api.example.com/repos/o/r",
            "token",
            "tracker-sync/20260218",
        )
        assert result is None

    @patch("hypergumbo_tracker.sync._api_call")
    def test_non_list_body(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (200, {"error": "bad"})
        result = _find_open_pr(
            "https://api.example.com/repos/o/r",
            "token",
            "tracker-sync/20260218",
        )
        assert result is None

    @patch("hypergumbo_tracker.sync._api_call")
    def test_found_by_title_agit_flow(self, mock_api: MagicMock) -> None:
        """AGit flow PRs have refs/pull/N/head as ref — match by title."""
        mock_api.return_value = (
            200,
            [
                {
                    "number": 77,
                    "title": "tracker: sync 1 file(s)",
                    "head": {"ref": "refs/pull/77/head", "sha": "aaa111"},
                },
            ],
        )
        result = _find_open_pr(
            "https://api.example.com/repos/o/r",
            "token",
            "tracker-sync/20260218",
            title="tracker: sync 1 file(s)",
        )
        assert result == (77, "aaa111")

    @patch("hypergumbo_tracker.sync._api_call")
    def test_title_fallback_not_used_when_branch_matches(
        self, mock_api: MagicMock
    ) -> None:
        """Branch match takes priority over title match."""
        mock_api.return_value = (
            200,
            [
                {
                    "number": 10,
                    "title": "tracker: sync 1 file(s)",
                    "head": {
                        "ref": "tracker-sync/20260218",
                        "sha": "bbb222",
                    },
                },
            ],
        )
        result = _find_open_pr(
            "https://api.example.com/repos/o/r",
            "token",
            "tracker-sync/20260218",
            title="tracker: sync 1 file(s)",
        )
        assert result == (10, "bbb222")

    @patch("hypergumbo_tracker.sync._api_call")
    def test_title_fallback_no_match(self, mock_api: MagicMock) -> None:
        """No match when neither branch nor title match."""
        mock_api.return_value = (
            200,
            [
                {
                    "number": 5,
                    "title": "feat: something else",
                    "head": {"ref": "refs/pull/5/head", "sha": "ccc333"},
                },
            ],
        )
        result = _find_open_pr(
            "https://api.example.com/repos/o/r",
            "token",
            "tracker-sync/20260218",
            title="tracker: sync 1 file(s)",
        )
        assert result is None

    @patch("hypergumbo_tracker.sync._api_call")
    def test_no_title_fallback_without_title_arg(
        self, mock_api: MagicMock
    ) -> None:
        """Without title arg, AGit flow PR is not found."""
        mock_api.return_value = (
            200,
            [
                {
                    "number": 77,
                    "title": "tracker: sync 1 file(s)",
                    "head": {"ref": "refs/pull/77/head", "sha": "aaa111"},
                },
            ],
        )
        result = _find_open_pr(
            "https://api.example.com/repos/o/r",
            "token",
            "tracker-sync/20260218",
        )
        assert result is None


# ---------------------------------------------------------------------------
# TestPollCi
# ---------------------------------------------------------------------------


class TestPollCi:
    """Tests for _poll_ci — CI status polling."""

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_immediate_success(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        mock_time.monotonic.side_effect = [0, 1, 2]
        mock_time.sleep = MagicMock()
        mock_api.return_value = (
            200,
            {"state": "success", "statuses": [
                {"status": "success", "context": "CI / pytest"},
            ]},
        )
        result = _poll_ci(
            "https://api.example.com/repos/o/r", "token", "sha123"
        )
        assert result == "success"

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_pending_then_success(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        # deadline(0)+start(10)+while(20)+stale_check(30)+while(40)
        mock_time.monotonic.side_effect = [0, 10, 20, 30, 40]
        mock_time.sleep = MagicMock()
        mock_api.side_effect = [
            (200, {"state": "pending", "statuses": [
                {"status": "pending", "context": "CI / pytest"},
            ]}),
            (200, {"state": "success", "statuses": [
                {"status": "success", "context": "CI / pytest"},
            ]}),
        ]
        result = _poll_ci(
            "https://api.example.com/repos/o/r",
            "token",
            "sha123",
            poll_interval=5,
            timeout=300,
        )
        assert result == "success"

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_failure(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        mock_time.monotonic.side_effect = [0, 0, 1]
        mock_time.sleep = MagicMock()
        mock_api.return_value = (
            200,
            {"state": "failure", "statuses": [
                {"status": "failure", "context": "CI / pytest"},
            ]},
        )
        result = _poll_ci(
            "https://api.example.com/repos/o/r", "token", "sha123"
        )
        assert result == "failure"

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_error_state(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        mock_time.monotonic.side_effect = [0, 0, 1]
        mock_time.sleep = MagicMock()
        mock_api.return_value = (
            200,
            {"state": "error", "statuses": [
                {"status": "error", "context": "CI / pytest"},
            ]},
        )
        result = _poll_ci(
            "https://api.example.com/repos/o/r", "token", "sha123"
        )
        assert result == "failure"

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_timeout(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        # deadline(0)+start(5)+while(10)+stale_check(20)+while(301)
        mock_time.monotonic.side_effect = [0, 5, 10, 20, 301]
        mock_time.sleep = MagicMock()
        mock_api.return_value = (
            200,
            {"state": "pending", "statuses": [
                {"status": "pending", "context": "CI / pytest"},
            ]},
        )
        result = _poll_ci(
            "https://api.example.com/repos/o/r",
            "token",
            "sha123",
            timeout=300,
        )
        assert result == "timeout"

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_empty_statuses_waits_for_ci(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        """Don't return success when no statuses exist — CI hasn't started."""
        mock_time.monotonic.side_effect = [0, 5, 10, 15, 20]
        mock_time.sleep = MagicMock()
        mock_api.side_effect = [
            (200, {"state": "success", "statuses": []}),  # no CI yet
            (200, {"state": "success", "statuses": []}),  # still nothing
            (200, {"state": "success", "statuses": [      # CI finished
                {"status": "success", "context": "CI / pytest"},
            ]}),
        ]
        result = _poll_ci(
            "https://api.example.com/repos/o/r",
            "token",
            "sha123",
            timeout=300,
        )
        assert result == "success"
        # Should have slept twice while waiting for statuses
        assert mock_time.sleep.call_count == 2

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_sole_holdout_bypass(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        # deadline(0)+start(61)+while(62)+holdout_check(63)
        mock_time.monotonic.side_effect = [0, 61, 62, 63]
        mock_time.sleep = MagicMock()
        mock_api.return_value = (
            200,
            {
                "state": "pending",
                "statuses": [
                    {"status": "success", "context": "tracker-ci"},
                    {"status": "pending", "context": "other-ci"},
                ],
            },
        )
        result = _poll_ci(
            "https://api.example.com/repos/o/r",
            "token",
            "sha123",
            timeout=300,
        )
        assert result == "success"

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_api_error_retries(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        # API fails, then succeeds
        mock_time.monotonic.side_effect = [0, 5, 10, 15]
        mock_time.sleep = MagicMock()
        mock_api.side_effect = [
            (0, None),  # network error
            (200, {"state": "success", "statuses": [
                {"status": "success", "context": "CI / pytest"},
            ]}),  # success
        ]
        result = _poll_ci(
            "https://api.example.com/repos/o/r",
            "token",
            "sha123",
            timeout=300,
        )
        assert result == "success"

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_non_dict_body_retries(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        # deadline(0)+start(5)+while(10)+while(301)
        mock_time.monotonic.side_effect = [0, 5, 10, 301]
        mock_time.sleep = MagicMock()
        mock_api.return_value = (200, [])  # non-dict body
        result = _poll_ci(
            "https://api.example.com/repos/o/r",
            "token",
            "sha123",
            timeout=300,
        )
        assert result == "timeout"

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_stale_pending_detection(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        """Return stale_pending when all jobs stay pending past threshold."""
        # monotonic: start=0, first poll at 5, second at 95 (past 90s threshold)
        mock_time.monotonic.side_effect = [0, 0, 5, 5, 95, 95]
        mock_time.sleep = MagicMock()
        mock_api.return_value = (
            200,
            {"state": "pending", "statuses": [
                {"status": "pending", "context": "Tracker CI / tracker-ci"},
            ]},
        )
        result = _poll_ci(
            "https://api.example.com/repos/o/r",
            "token",
            "sha123",
            timeout=600,
            stale_pending_threshold=90,
        )
        assert result == "stale_pending"

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_stale_pending_not_triggered_when_job_starts(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        """No stale_pending if a job transitions out of pending."""
        # First poll: pending. Second poll (past threshold): success.
        mock_time.monotonic.side_effect = [0, 0, 5, 5, 95, 95]
        mock_time.sleep = MagicMock()
        mock_api.side_effect = [
            (200, {"state": "pending", "statuses": [
                {"status": "pending", "context": "Tracker CI / tracker-ci"},
            ]}),
            (200, {"state": "success", "statuses": [
                {"status": "success", "context": "Tracker CI / tracker-ci"},
            ]}),
        ]
        result = _poll_ci(
            "https://api.example.com/repos/o/r",
            "token",
            "sha123",
            timeout=600,
            stale_pending_threshold=90,
        )
        assert result == "success"

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._api_call")
    def test_stale_pending_skipped_when_running(
        self, mock_api: MagicMock, mock_time: MagicMock
    ) -> None:
        """No stale_pending if a job is running (not pending) before threshold."""
        mock_time.monotonic.side_effect = [0, 0, 50, 50, 95, 95, 120, 120]
        mock_time.sleep = MagicMock()
        mock_api.side_effect = [
            (200, {"state": "pending", "statuses": [
                {"status": "pending", "context": "a"},
                {"status": "success", "context": "b"},  # one already done
            ]}),
            (200, {"state": "pending", "statuses": [
                {"status": "pending", "context": "a"},
                {"status": "success", "context": "b"},
            ]}),
            (200, {"state": "success", "statuses": [
                {"status": "success", "context": "a"},
                {"status": "success", "context": "b"},
            ]}),
        ]
        result = _poll_ci(
            "https://api.example.com/repos/o/r",
            "token",
            "sha123",
            timeout=600,
            stale_pending_threshold=90,
        )
        assert result == "success"


# ---------------------------------------------------------------------------
# TestClosePr
# ---------------------------------------------------------------------------


class TestClosePr:
    """Tests for _close_pr."""

    @patch("hypergumbo_tracker.sync._api_call")
    def test_close_success(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (200, {"state": "closed"})
        assert _close_pr("https://api.example.com/repos/o/r", "tok", 42)
        mock_api.assert_called_once_with(
            "PATCH",
            "https://api.example.com/repos/o/r/pulls/42",
            "tok",
            data={"state": "closed"},
        )

    @patch("hypergumbo_tracker.sync._api_call")
    def test_close_failure(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (404, None)
        assert not _close_pr("https://api.example.com/repos/o/r", "tok", 99)


# ---------------------------------------------------------------------------
# TestMergePr
# ---------------------------------------------------------------------------


class TestLog:
    """Tests for _log — stderr diagnostic helper."""

    def test_writes_to_stderr(self, capsys: Any) -> None:
        _log("hello world")
        assert capsys.readouterr().err == "sync: hello world\n"


class TestCheckPrMerged:
    """Tests for _check_pr_merged — verify PR merged state."""

    @patch("hypergumbo_tracker.sync._api_call")
    def test_merged(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (200, {"merged": True})
        assert _check_pr_merged("https://api.example.com/repos/o/r", "t", 1)

    @patch("hypergumbo_tracker.sync._api_call")
    def test_not_merged(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (200, {"merged": False})
        assert not _check_pr_merged(
            "https://api.example.com/repos/o/r", "t", 1
        )

    @patch("hypergumbo_tracker.sync._api_call")
    def test_api_failure(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (0, None)
        assert not _check_pr_merged(
            "https://api.example.com/repos/o/r", "t", 1
        )

    @patch("hypergumbo_tracker.sync._api_call")
    def test_non_dict_body(self, mock_api: MagicMock) -> None:
        mock_api.return_value = (200, [])
        assert not _check_pr_merged(
            "https://api.example.com/repos/o/r", "t", 1
        )


class TestMergePr:
    """Tests for _merge_pr — cascading merge strategies."""

    BASE = "https://api.example.com/repos/o/r"

    @patch("hypergumbo_tracker.sync._api_call")
    def test_fast_forward_204(self, mock_api: MagicMock) -> None:
        """Fast-forward returns 204 — immediate success."""
        mock_api.return_value = (204, None)
        assert _merge_pr(self.BASE, "token", 42)
        # Only one call (fast-forward), no fallback needed
        assert mock_api.call_count == 1

    @patch("hypergumbo_tracker.sync._api_call")
    def test_fast_forward_200_verified(self, mock_api: MagicMock) -> None:
        """Fast-forward returns 200, verified merged via GET."""
        mock_api.side_effect = [
            (200, {"merged": False}),  # POST ff — ambiguous 200
            (200, {"merged": True}),   # GET check — actually merged
        ]
        assert _merge_pr(self.BASE, "token", 42)
        assert mock_api.call_count == 2

    @patch("hypergumbo_tracker.sync._api_call")
    def test_fast_forward_200_not_merged_rebase_succeeds(
        self, mock_api: MagicMock
    ) -> None:
        """Fast-forward 200 but not merged, rebase 204 succeeds."""
        mock_api.side_effect = [
            (200, {"merged": False}),  # POST ff
            (200, {"merged": False}),  # GET check — not merged
            (204, None),               # POST rebase — success
        ]
        assert _merge_pr(self.BASE, "token", 42)
        assert mock_api.call_count == 3

    @patch("hypergumbo_tracker.sync._api_call")
    def test_all_strategies_fail(self, mock_api: MagicMock) -> None:
        """All three strategies fail — returns False."""
        mock_api.side_effect = [
            (500, {"message": "error"}),  # POST ff
            (500, {"message": "error"}),  # POST rebase
            (500, {"message": "error"}),  # POST merge
        ]
        assert not _merge_pr(self.BASE, "token", 42)

    @patch("hypergumbo_tracker.sync._api_call")
    def test_ff_and_rebase_fail_merge_commit_succeeds(
        self, mock_api: MagicMock
    ) -> None:
        """Fast-forward and rebase both fail, merge commit works."""
        mock_api.side_effect = [
            (200, {"merged": False}),  # POST ff
            (200, {"merged": False}),  # GET check — not merged
            (200, {"merged": False}),  # POST rebase
            (200, {"merged": False}),  # GET check — not merged
            (200, {"merged": False}),  # POST merge
            (200, {"merged": True}),   # GET check — merged!
        ]
        assert _merge_pr(self.BASE, "token", 42)
        assert mock_api.call_count == 6

    @patch("hypergumbo_tracker.sync._api_call")
    def test_already_merged_405(self, mock_api: MagicMock) -> None:
        """405 on fast-forward, PR already merged."""
        mock_api.side_effect = [
            (405, {"message": "already merged"}),
            (200, {"merged": True}),  # GET check — already merged
        ]
        assert _merge_pr(self.BASE, "token", 42)

    @patch("hypergumbo_tracker.sync._api_call")
    def test_409_not_merged_falls_through(
        self, mock_api: MagicMock
    ) -> None:
        """409 on fast-forward, not merged, tries next strategy."""
        mock_api.side_effect = [
            (409, {"message": "conflict"}),   # POST ff
            (200, {"merged": False}),          # GET check
            (204, None),                       # POST rebase — success
        ]
        assert _merge_pr(self.BASE, "token", 42)
        assert mock_api.call_count == 3

    @patch("hypergumbo_tracker.sync._api_call")
    def test_405_api_check_fails_tries_next(
        self, mock_api: MagicMock
    ) -> None:
        """405 on ff, API check fails, tries rebase."""
        mock_api.side_effect = [
            (405, {"message": "blocked"}),
            (0, None),    # GET check fails
            (204, None),  # POST rebase — success
        ]
        assert _merge_pr(self.BASE, "token", 42)

    @patch("hypergumbo_tracker.sync._api_call")
    def test_all_blocked_all_not_merged(
        self, mock_api: MagicMock
    ) -> None:
        """All strategies get 405/409, never actually merged."""
        mock_api.side_effect = [
            (405, {"message": "blocked"}),
            (200, {"merged": False}),  # GET check
            (409, {"message": "conflict"}),
            (200, {"merged": False}),  # GET check
            (405, {"message": "blocked"}),
            (200, {"merged": False}),  # GET check
        ]
        assert not _merge_pr(self.BASE, "token", 42)


# ---------------------------------------------------------------------------
# TestPreflightCheck
# ---------------------------------------------------------------------------


class TestPreflightCheck:
    """Tests for preflight_check — sequential pre-sync validation."""

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_not_a_git_repo(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_git.return_value = _make_completed_process(
            returncode=1, stderr="not a git repo"
        )
        result = preflight_check(tmp_path)
        assert not result.ok
        assert "not a git repository" in result.error

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_pr_pending_gate(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "PR_PENDING").write_text("123\n")
        mock_git.return_value = _make_completed_process(
            stdout=str(git_dir)
        )
        result = preflight_check(tmp_path)
        assert not result.ok
        assert "auto-pr in flight" in result.error

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_sync_pending_gate(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "TRACKER_SYNC_PENDING").write_text("sync\n")
        mock_git.return_value = _make_completed_process(
            stdout=str(git_dir)
        )
        result = preflight_check(tmp_path)
        assert not result.ok
        assert "already in progress" in result.error

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_no_write_access_to_refs_heads(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Fail fast when user lacks write access to .git/refs/heads/."""
        git_dir = tmp_path / ".git"
        refs_heads = git_dir / "refs" / "heads"
        refs_heads.mkdir(parents=True)
        mock_git.return_value = _make_completed_process(
            stdout=str(git_dir)
        )
        with patch("hypergumbo_tracker.sync.os.access", return_value=False):
            result = preflight_check(tmp_path)
        assert not result.ok
        assert "no write access" in result.error

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_detached_head(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),  # rev-parse
            _make_completed_process(returncode=1),  # branch --show-current
        ]
        result = preflight_check(tmp_path)
        assert not result.ok
        assert "detached HEAD" in result.error

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_staged_non_tracker_files(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),  # rev-parse
            _make_completed_process(stdout="dev\n"),  # branch
            _make_completed_process(stdout="src/main.py\n"),  # diff --cached
        ]
        result = preflight_check(tmp_path)
        assert not result.ok
        assert "non-tracker files" in result.error

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_nothing_to_sync(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),  # rev-parse
            _make_completed_process(stdout="dev\n"),  # branch
            _make_completed_process(stdout=""),  # diff --cached (empty)
            _make_completed_process(stdout=""),  # status --porcelain (empty)
        ]
        result = preflight_check(tmp_path)
        assert result.ok
        assert result.changed_files == []

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_no_token(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.delenv("FORGEJO_TOKEN", raising=False)
        mock_env.return_value = {}
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),  # rev-parse
            _make_completed_process(stdout="dev\n"),  # branch
            _make_completed_process(stdout=""),  # diff --cached
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),  # status
        ]
        result = preflight_check(tmp_path)
        assert not result.ok
        assert "FORGEJO_TOKEN" in result.error

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_no_git_identity(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        mock_env.return_value = {"FORGEJO_TOKEN": "tok"}
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),  # rev-parse
            _make_completed_process(stdout="dev\n"),  # branch
            _make_completed_process(stdout=""),  # diff --cached
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),  # status
            _make_completed_process(stdout=""),  # user.name (empty)
            _make_completed_process(stdout=""),  # user.email (empty)
        ]
        result = preflight_check(tmp_path)
        assert not result.ok
        assert "git identity" in result.error

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_no_remote(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        mock_env.return_value = {"FORGEJO_TOKEN": "tok"}
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),  # rev-parse
            _make_completed_process(stdout="dev\n"),  # branch
            _make_completed_process(stdout=""),  # diff --cached
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),  # status
            _make_completed_process(stdout="Test User\n"),  # user.name
            _make_completed_process(stdout="test@test.com\n"),  # user.email
            _make_completed_process(returncode=1),  # remote get-url
        ]
        result = preflight_check(tmp_path)
        assert not result.ok
        assert "no remote" in result.error

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_no_api_base(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        mock_env.return_value = {"FORGEJO_TOKEN": "tok"}
        mock_api_base.return_value = ""
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),  # rev-parse
            _make_completed_process(stdout="dev\n"),  # branch
            _make_completed_process(stdout=""),  # diff --cached
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),  # status
            _make_completed_process(stdout="Test User\n"),  # user.name
            _make_completed_process(stdout="test@test.com\n"),  # user.email
            _make_completed_process(
                stdout="https://codeberg.org/o/r.git\n"
            ),  # remote
        ]
        result = preflight_check(tmp_path)
        assert not result.ok
        assert "API URL" in result.error

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_happy_path(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        mock_env.return_value = {
            "FORGEJO_TOKEN": "tok",
            "FORGEJO_USER": "user",
        }
        mock_api_base.return_value = (
            "https://codeberg.org/api/v1/repos/o/r"
        )
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),  # rev-parse
            _make_completed_process(stdout="dev\n"),  # branch
            _make_completed_process(stdout=""),  # diff --cached
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),  # status
            _make_completed_process(stdout="Test User\n"),  # user.name
            _make_completed_process(stdout="test@test.com\n"),  # user.email
            _make_completed_process(
                stdout="https://codeberg.org/o/r.git\n"
            ),  # remote
        ]
        result = preflight_check(tmp_path)
        assert result.ok
        assert result.original_branch == "dev"
        assert len(result.changed_files) == 1
        assert result.forgejo_token == "tok"
        assert result.forgejo_user == "user"

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_happy_path_with_failover(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Preflight detects failover and overrides credentials/remote."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        # Write failover flag file
        (git_dir / "CI_FAILOVER_ACTIVE").write_text(json.dumps({
            "selfhosted_forgejo_url": "http://10.85.0.10:3000",
            "selfhosted_forgejo_repo": "admin-josh/hypergumbo",
        }))
        mock_env.return_value = {
            "FORGEJO_TOKEN": "codeberg-tok",
            "FORGEJO_USER": "codeberg-user",
            "SELFHOSTED_FORGEJO_TOKEN": "local-tok",
            "SELFHOSTED_FORGEJO_USER": "local-user",
        }
        mock_api_base.return_value = (
            "https://codeberg.org/api/v1/repos/o/r"
        )
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),  # rev-parse
            _make_completed_process(stdout="dev\n"),  # branch
            _make_completed_process(stdout=""),  # diff --cached
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),  # status
            _make_completed_process(stdout="Test User\n"),  # user.name
            _make_completed_process(stdout="test@test.com\n"),  # user.email
            _make_completed_process(
                stdout="http://10.85.0.10:3000/admin-josh/hypergumbo.git\n"
            ),  # remote get-url local
        ]
        result = preflight_check(tmp_path)
        assert result.ok
        assert result.push_remote == "selfh"
        assert result.forgejo_token == "local-tok"
        assert result.forgejo_user == "local-user"
        assert result.api_base == (
            "http://10.85.0.10:3000/api/v1/repos/admin-josh/hypergumbo"
        )
        # _detect_api_base should NOT have been used for the final result
        # (failover overrides it)

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_relative_git_dir(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When git rev-parse --git-dir returns a relative path."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        mock_env.return_value = {"FORGEJO_TOKEN": "tok"}
        mock_api_base.return_value = "https://codeberg.org/api/v1/repos/o/r"
        mock_git.side_effect = [
            _make_completed_process(stdout=".git"),  # relative path
            _make_completed_process(stdout="dev\n"),  # branch
            _make_completed_process(stdout=""),  # diff --cached
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),  # status
            _make_completed_process(stdout="User\n"),  # user.name
            _make_completed_process(stdout="u@e.com\n"),  # user.email
            _make_completed_process(stdout="https://cb.org/o/r.git\n"),  # remote
        ]
        result = preflight_check(tmp_path)
        assert result.ok
        assert result.git_dir == tmp_path / ".git"

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_staged_tracker_files_ok(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Staged tracker files don't cause abort."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        mock_env.return_value = {"FORGEJO_TOKEN": "tok"}
        mock_api_base.return_value = "https://codeberg.org/api/v1/repos/o/r"
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),
            _make_completed_process(stdout="dev\n"),
            # Staged tracker file — should NOT abort
            _make_completed_process(
                stdout=".agent/tracker/.ops/.WI-test.ops\n"
            ),
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),
            _make_completed_process(stdout="User\n"),
            _make_completed_process(stdout="u@e.com\n"),
            _make_completed_process(stdout="https://cb.org/o/r.git\n"),
        ]
        result = preflight_check(tmp_path)
        assert result.ok

    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_token_from_env_var(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FORGEJO_TOKEN from os.environ is accepted."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        monkeypatch.setenv("FORGEJO_TOKEN", "envtok")
        mock_env.return_value = {}  # no .env
        mock_api_base.return_value = "https://codeberg.org/api/v1/repos/o/r"
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),
            _make_completed_process(stdout="dev\n"),
            _make_completed_process(stdout=""),
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),
            _make_completed_process(stdout="User\n"),
            _make_completed_process(stdout="u@e.com\n"),
            _make_completed_process(stdout="https://cb.org/o/r.git\n"),
        ]
        result = preflight_check(tmp_path)
        assert result.ok
        assert result.forgejo_token == "envtok"

    @patch("hypergumbo_tracker.sync.validate_all")
    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_validation_failure_blocks_sync(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Pre-sync validation catches dangling refs before push."""
        from hypergumbo_tracker.validation import ValidationResult

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        # Set up tracker directory so validate_all has a target
        tracker_root = tmp_path / ".agent"
        (tracker_root / "tracker" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)

        mock_env.return_value = {"FORGEJO_TOKEN": "tok"}
        mock_api_base.return_value = "https://codeberg.org/api/v1/repos/o/r"
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),  # rev-parse
            _make_completed_process(stdout="dev\n"),  # branch
            _make_completed_process(stdout=""),  # diff --cached
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),  # status — dirty files exist
        ]
        bad_result = ValidationResult()
        bad_result.errors.append(
            "WI-test: dangling parent reference 'INV-short'"
        )
        mock_validate.return_value = bad_result

        result = preflight_check(tmp_path)
        assert not result.ok
        assert "tracker validation failed" in result.error
        assert "dangling parent" in result.error

    @patch("hypergumbo_tracker.sync.validate_all")
    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_validation_many_errors_truncated(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When >5 validation errors, message shows truncation count."""
        from hypergumbo_tracker.validation import ValidationResult

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        tracker_root = tmp_path / ".agent"
        (tracker_root / "tracker" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)

        mock_env.return_value = {"FORGEJO_TOKEN": "tok"}
        mock_api_base.return_value = "https://codeberg.org/api/v1/repos/o/r"
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),
            _make_completed_process(stdout="dev\n"),
            _make_completed_process(stdout=""),
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),
        ]
        bad_result = ValidationResult()
        for i in range(7):
            bad_result.errors.append(f"error-{i}: some problem")
        mock_validate.return_value = bad_result

        result = preflight_check(tmp_path)
        assert not result.ok
        assert "(and 2 more)" in result.error

    @patch("hypergumbo_tracker.sync.validate_all")
    @patch("hypergumbo_tracker.sync._git")
    @patch("hypergumbo_tracker.sync._load_env")
    @patch("hypergumbo_tracker.sync._detect_api_base")
    def test_validation_pass_proceeds(
        self,
        mock_api_base: MagicMock,
        mock_env: MagicMock,
        mock_git: MagicMock,
        mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Pre-sync validation passing allows sync to proceed."""
        from hypergumbo_tracker.validation import ValidationResult

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        tracker_root = tmp_path / ".agent"
        (tracker_root / "tracker" / ".ops").mkdir(parents=True)
        (tracker_root / "tracker-workspace" / ".ops").mkdir(parents=True)

        mock_env.return_value = {"FORGEJO_TOKEN": "tok"}
        mock_api_base.return_value = "https://codeberg.org/api/v1/repos/o/r"
        mock_git.side_effect = [
            _make_completed_process(stdout=str(git_dir)),  # rev-parse
            _make_completed_process(stdout="dev\n"),  # branch
            _make_completed_process(stdout=""),  # diff --cached
            _make_completed_process(
                stdout=" M .agent/tracker/.ops/.WI-test.ops\n"
            ),  # status — dirty files
            _make_completed_process(stdout="User\n"),  # user.name
            _make_completed_process(stdout="u@e.com\n"),  # user.email
            _make_completed_process(stdout="https://cb.org/o/r.git\n"),
        ]
        mock_validate.return_value = ValidationResult()  # no errors

        result = preflight_check(tmp_path)
        assert result.ok


# ---------------------------------------------------------------------------
# TestDoSync
# ---------------------------------------------------------------------------


class TestDoSync:
    """Tests for do_sync — full workflow with all externals mocked.

    The plumbing call sequence is:
      fetch, rev-parse, read-tree, add, write-tree,
      config user.name, config user.email, commit-tree, update-ref,

    Note: init_sync_log is auto-mocked via the fixture below to prevent
    file I/O during tests (creating .agent/.sync-logs/ in tmp_path).
      push, ..., pull, branch -D
    """

    @pytest.fixture(autouse=True)
    def _mock_init_sync_log(self) -> Any:
        with patch("hypergumbo_tracker.sync.init_sync_log"):
            yield

    @staticmethod
    def _plumbing_setup() -> list[Any]:
        """Return git mock side_effect entries for the 9 plumbing setup calls."""
        return [
            _make_completed_process(),                       # fetch
            _make_completed_process(stdout="abc123\n"),       # rev-parse
            _make_completed_process(),                       # read-tree
            _make_completed_process(),                       # add
            _make_completed_process(stdout="tree456\n"),      # write-tree
            _make_completed_process(stdout="Test User\n"),    # config user.name
            _make_completed_process(stdout="t@e.com\n"),      # config user.email
            _make_completed_process(stdout="commit789\n"),    # commit-tree
            _make_completed_process(),                       # update-ref
        ]

    @staticmethod
    def _rebase_check_no_diverge() -> list[Any]:
        """Return git mock entries for post-CI rebase check (no divergence)."""
        return [
            _make_completed_process(),                       # fetch (re-fetch)
            _make_completed_process(stdout="abc123\n"),       # rev-parse (same base)
        ]

    @staticmethod
    def _rebase_check_diverged() -> list[Any]:
        """Return git mock entries for post-CI rebase check (dev diverged).

        Returns entries for: fetch, rev-parse (new base), read-tree,
        add, write-tree, commit-tree, update-ref, force-push.
        """
        return [
            _make_completed_process(),                          # fetch (re-fetch)
            _make_completed_process(stdout="newbase999\n"),      # rev-parse (diverged!)
            _make_completed_process(),                          # read-tree (new base)
            _make_completed_process(),                          # add (stage ops)
            _make_completed_process(stdout="newtree111\n"),      # write-tree
            _make_completed_process(stdout="newcommit222\n"),    # commit-tree
            _make_completed_process(),                          # update-ref
            _make_completed_process(),                          # force-push
        ]

    @staticmethod
    def _cleanup(on_base_branch: bool = True) -> list[Any]:
        """Return git mock side_effect entries for cleanup calls.

        When on_base_branch is True (default, matching _make_preflight's
        original_branch="dev"), includes the checkout/ls-files/merge
        calls that absorb synced ops files into the local branch.
        """
        entries = [
            _make_completed_process(),  # fetch (update remote tracking ref)
        ]
        if on_base_branch:
            entries.extend([
                _make_completed_process(),  # checkout HEAD -- (reset tracked)
                _make_completed_process(stdout=""),  # ls-files --others (none)
                _make_completed_process(),  # merge --ff-only origin/dev
            ])
        entries.append(
            _make_completed_process(),  # branch -D
        )
        return entries

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_happy_path(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_no_diverge(),
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)

        assert result.success
        assert result.pr_number == 42
        assert result.files_synced == 1
        assert result.exit_code == 0
        assert "pulls/42" in result.pr_url

        # Gate file should be cleaned up
        assert not (tmp_path / ".git" / "TRACKER_SYNC_PENDING").exists()
        # Temp index should be cleaned up
        assert not (tmp_path / ".git" / "tmp-sync-index").exists()

        # Cleanup: fetch, checkout, ls-files, ff-only merge, branch -D
        cleanup_calls = mock_git.call_args_list[-5:]
        assert "fetch" in cleanup_calls[0][0]
        assert "checkout" in cleanup_calls[1][0]
        assert "ls-files" in cleanup_calls[2][0]
        merge_args = cleanup_calls[3][0]
        assert "merge" in merge_args
        assert "--ff-only" in merge_args

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_no_ff_merge_on_feature_branch(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When syncing from a feature branch, skip the ff-only merge."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path, original_branch="feat/something")

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_no_diverge(),
            *self._cleanup(on_base_branch=False),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success

        # No merge --ff-only call in cleanup (only fetch + branch -D)
        cleanup_calls = mock_git.call_args_list[-2:]
        for git_call in cleanup_calls:
            git_args = git_call[0]
            assert "merge" not in git_args, (
                f"Should not merge on feature branch, got: {git_args}"
            )

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_ff_merge_removes_untracked_ops_files(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Untracked ops files are removed before ff-only merge to prevent
        git from refusing to overwrite them."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        ops_file = ".agent/tracker/.ops/.WI-test.ops"
        pre = _make_preflight(tmp_path, changed_files=[ops_file])

        # Create the ops file on disk so unlink() actually fires
        ops_path = tmp_path / ops_file
        ops_path.parent.mkdir(parents=True, exist_ok=True)
        ops_path.write_text("some ops data\n")

        # Custom cleanup: ls-files returns the untracked file path
        cleanup_with_untracked = [
            _make_completed_process(),  # fetch
            _make_completed_process(),  # checkout HEAD --
            _make_completed_process(stdout=f"{ops_file}\n"),  # ls-files
            _make_completed_process(),  # merge --ff-only
            _make_completed_process(),  # branch -D
        ]

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_no_diverge(),
            *cleanup_with_untracked,
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success

        # Ops file should have been removed before ff-only merge
        assert not ops_path.exists(), (
            "Ops file should be removed before ff-only merge"
        )

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_commit_disables_gpg_signing(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Tracker sync commits bypass GPG signing to avoid passphrase prompts."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_no_diverge(),
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success

        # Find the commit-tree call (8th git call, index 7)
        commit_call = mock_git.call_args_list[7]
        commit_args = commit_call[0]  # positional args
        # Should include -c commit.gpgSign=false before "commit-tree"
        assert "-c" in commit_args
        assert "commit.gpgSign=false" in commit_args

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._git")
    def test_rev_parse_failure(
        self,
        mock_git: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            _make_completed_process(),  # fetch
            _make_completed_process(
                returncode=1, stderr="unknown revision"
            ),  # rev-parse fails
            *self._cleanup(),
        ]

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert "cannot resolve" in result.error

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._git")
    def test_commit_tree_failure(
        self,
        mock_git: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            _make_completed_process(),                       # fetch
            _make_completed_process(stdout="abc123\n"),       # rev-parse
            _make_completed_process(),                       # read-tree
            _make_completed_process(),                       # add
            _make_completed_process(stdout="tree456\n"),      # write-tree
            _make_completed_process(stdout="Test User\n"),    # config user.name
            _make_completed_process(stdout="t@e.com\n"),      # config user.email
            _make_completed_process(
                returncode=1, stderr="bad tree object"
            ),  # commit-tree fails
            *self._cleanup(),
        ]

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert "commit-tree failed" in result.error

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._git")
    def test_push_failure_with_retries(
        self,
        mock_git: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            # 3 push failures
            _make_completed_process(returncode=1),
            _make_completed_process(returncode=1),
            _make_completed_process(returncode=1),
            *self._cleanup(),
        ]

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert "push failed" in result.error

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_pr_not_found(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._cleanup(),
        ]
        mock_find_pr.return_value = None

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert "could not find" in result.error

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_ci_failure(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "failure"

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert result.pr_number == 42
        assert result.exit_code == 1

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_ci_timeout(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "timeout"

        result = do_sync(
            repo_root=tmp_path, preflight=pre, ci_timeout=300
        )
        assert not result.success
        assert result.exit_code == 2
        assert "timed out" in result.error

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_merge_failure_after_retries(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_no_diverge(),
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = False

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert "merge failed after retries" in result.error
        # Should have retried 6 times
        assert mock_merge.call_count == 6

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_merge_succeeds_on_retry(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Merge fails initially (status checks not ready), succeeds on retry."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_no_diverge(),
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        # Fail twice, succeed on third attempt
        mock_merge.side_effect = [False, False, True]

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success
        assert result.pr_number == 42
        assert mock_merge.call_count == 3

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_gate_file_cleanup_on_error(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Gate file is removed even when sync fails."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "failure"

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert not (tmp_path / ".git" / "TRACKER_SYNC_PENDING").exists()

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._git")
    def test_push_failure_no_branch_restore_needed(
        self,
        mock_git: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """With plumbing approach, push failure needs no ops restore.

        Unlike the old checkout-based approach, the plumbing approach never
        leaves the original branch, so ops files remain in the working tree.
        """
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            # 3 push failures
            _make_completed_process(returncode=1),
            _make_completed_process(returncode=1),
            _make_completed_process(returncode=1),
            *self._cleanup(),
        ]

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert "push failed" in result.error

        # Verify no branch-switching checkout calls.  The cleanup phase
        # uses ``checkout HEAD -- <paths>`` to reset ops file content
        # (not switch branches), which is fine.
        all_calls = mock_git.call_args_list
        branch_checkout_calls = [
            c for c in all_calls
            if len(c[0]) >= 2
            and c[0][1] == "checkout"
            and "HEAD" not in c[0]
        ]
        assert len(branch_checkout_calls) == 0, (
            f"Expected no branch checkout calls, got "
            f"{len(branch_checkout_calls)}. "
            f"All calls: {[c[0] for c in all_calls]}"
        )

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._git")
    def test_push_retry_then_success(
        self,
        mock_git: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Push succeeds on second attempt."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        with (
            patch("hypergumbo_tracker.sync._find_open_pr") as mock_find,
            patch("hypergumbo_tracker.sync._poll_ci") as mock_poll,
            patch("hypergumbo_tracker.sync._merge_pr") as mock_merge,
        ):
            mock_git.side_effect = [
                *self._plumbing_setup(),
                _make_completed_process(returncode=1),  # push attempt 1
                _make_completed_process(),  # push attempt 2 succeeds
                *self._rebase_check_no_diverge(),
                *self._cleanup(),
            ]
            mock_find.return_value = (42, "sha123")
            mock_poll.return_value = "success"
            mock_merge.return_value = True

            result = do_sync(repo_root=tmp_path, preflight=pre)
            assert result.success

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_sync_branches_from_origin_dev(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Sync commit is parented on origin/dev, not the current branch.

        This is the core fix: tracker sync PRs must only contain ops diffs,
        not feature branch code.
        """
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_no_diverge(),
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success

        # Verify commit-tree uses origin/dev SHA as parent
        commit_tree_call = mock_git.call_args_list[7]
        args = commit_tree_call[0]
        assert "commit-tree" in args
        assert "-p" in args
        p_idx = args.index("-p")
        assert args[p_idx + 1] == "abc123"  # base SHA from rev-parse

        # Verify push uses explicit ref, not HEAD
        push_call = mock_git.call_args_list[9]
        push_args = push_call[0]
        push_ref = [
            a for a in push_args
            if isinstance(a, str) and "refs/heads/" in a and "refs/for/" in a
        ]
        assert len(push_ref) == 1, f"Expected explicit push ref, got: {push_args}"

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._git")
    def test_read_tree_failure(
        self,
        mock_git: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            _make_completed_process(),                   # fetch
            _make_completed_process(stdout="abc123\n"),   # rev-parse
            _make_completed_process(
                returncode=1, stderr="not a tree"
            ),  # read-tree fails
            *self._cleanup(),
        ]

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert "read-tree failed" in result.error

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._git")
    def test_write_tree_failure(
        self,
        mock_git: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            _make_completed_process(),                   # fetch
            _make_completed_process(stdout="abc123\n"),   # rev-parse
            _make_completed_process(),                   # read-tree
            _make_completed_process(),                   # add
            _make_completed_process(
                returncode=1, stderr="cannot write"
            ),  # write-tree fails
            *self._cleanup(),
        ]

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert "write-tree failed" in result.error

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._git")
    def test_update_ref_failure(
        self,
        mock_git: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            _make_completed_process(),                       # fetch
            _make_completed_process(stdout="abc123\n"),       # rev-parse
            _make_completed_process(),                       # read-tree
            _make_completed_process(),                       # add
            _make_completed_process(stdout="tree456\n"),      # write-tree
            _make_completed_process(stdout="Test User\n"),    # config user.name
            _make_completed_process(stdout="t@e.com\n"),      # config user.email
            _make_completed_process(stdout="commit789\n"),    # commit-tree
            _make_completed_process(
                returncode=1, stderr="ref locked"
            ),  # update-ref fails
            *self._cleanup(),
        ]

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert "update-ref failed" in result.error

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_tmp_index_cleanup(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Temporary index file is cleaned up after sync."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        # Create the temp index file to verify cleanup
        tmp_index = tmp_path / ".git" / "tmp-sync-index"
        tmp_index.write_text("fake index")

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_no_diverge(),
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success
        assert not tmp_index.exists()

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_rebase_when_dev_diverges(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When dev advances during CI, sync rebases the commit before merging."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_diverged(),
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success
        assert result.pr_number == 42

        # Verify the rebase sequence happened:
        # After push (index 9), we expect:
        #   10: fetch (re-fetch)
        #   11: rev-parse (returns newbase999)
        #   12: read-tree (new base)
        #   13: add (stage ops)
        #   14: write-tree
        #   15: commit-tree
        #   16: update-ref
        #   17: force-push
        calls = mock_git.call_args_list

        # rev-parse after CI should return new base
        rebase_rev = calls[11]
        assert "rev-parse" in rebase_rev[0]

        # The rebased commit-tree should use new base as parent
        rebase_commit = calls[15]
        commit_args = rebase_commit[0]
        assert "commit-tree" in commit_args
        assert "-p" in commit_args
        p_idx = list(commit_args).index("-p")
        assert commit_args[p_idx + 1] == "newbase999"

        # Force-push should be present
        force_push_call = calls[17]
        assert "--force" in force_push_call[0]

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_rebase_read_tree_failure_skips_rebase(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If read-tree fails during rebase, skip rebase and try merge anyway."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            # Rebase check: dev diverged but read-tree fails
            _make_completed_process(),                          # fetch
            _make_completed_process(stdout="newbase999\n"),      # rev-parse
            _make_completed_process(returncode=1),              # read-tree FAILS
            # Falls through to merge attempt without rebasing
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        # Should still succeed if merge works despite stale base
        assert result.success

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_rebase_fetch_failure_uses_old_base(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If re-fetch fails, rev-parse returns old base, no rebase needed."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            # Rebase check: fetch fails, rev-parse returns same base
            _make_completed_process(returncode=1),              # fetch FAILS
            _make_completed_process(stdout="abc123\n"),           # rev-parse (same)
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_rebase_rev_parse_failure_uses_old_base(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If rev-parse fails after CI, fall back to old base (no rebase)."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            # Rebase check: fetch ok but rev-parse fails
            _make_completed_process(),                              # fetch
            _make_completed_process(returncode=1, stderr="err"),    # rev-parse FAILS
            *self._cleanup(),
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success


# ---------------------------------------------------------------------------
# TestCmdSync
# ---------------------------------------------------------------------------


class TestCmdSync:
    """Tests for the CLI handler _cmd_sync via main()."""

    @patch("hypergumbo_tracker.sync._git")
    def test_dry_run(
        self,
        mock_git: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_tracker.cli import main

        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        # Mock repo_root discovery
        with patch(
            "hypergumbo_tracker.sync.preflight_check"
        ) as mock_pre:
            mock_pre.return_value = PreflightResult(
                ok=True,
                repo_root=tmp_path,
                git_dir=git_dir,
                original_branch="dev",
                changed_files=[".agent/tracker/.ops/.WI-test.ops"],
            )
            # Mock the subprocess call for --show-toplevel
            mock_git.return_value = _make_completed_process(
                stdout=str(tmp_path)
            )
            with patch("subprocess.run") as mock_subproc:
                mock_subproc.return_value = _make_completed_process(
                    stdout=str(tmp_path)
                )
                with pytest.raises(SystemExit) as exc_info:
                    main(["sync", "--dry-run"])

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "would sync" in captured.out
        assert "WI-test" in captured.out

    @patch("subprocess.run")
    def test_nothing_to_sync(
        self,
        mock_subproc: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_tracker.cli import main

        mock_subproc.return_value = _make_completed_process(
            stdout=str(tmp_path)
        )

        with patch(
            "hypergumbo_tracker.sync.preflight_check"
        ) as mock_pre:
            mock_pre.return_value = PreflightResult(
                ok=True,
                repo_root=tmp_path,
                git_dir=tmp_path / ".git",
                original_branch="dev",
                changed_files=[],
            )
            with pytest.raises(SystemExit) as exc_info:
                main(["sync"])

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "nothing to sync" in captured.out

    @patch("subprocess.run")
    def test_preflight_error(
        self,
        mock_subproc: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_tracker.cli import main

        mock_subproc.return_value = _make_completed_process(
            stdout=str(tmp_path)
        )

        with patch(
            "hypergumbo_tracker.sync.preflight_check"
        ) as mock_pre:
            mock_pre.return_value = PreflightResult(
                ok=False, error="auto-pr in flight"
            )
            with pytest.raises(SystemExit) as exc_info:
                main(["sync"])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "auto-pr in flight" in captured.err

    @patch("subprocess.run")
    def test_sync_success(
        self,
        mock_subproc: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_tracker.cli import main

        mock_subproc.return_value = _make_completed_process(
            stdout=str(tmp_path)
        )

        with (
            patch("hypergumbo_tracker.sync.preflight_check") as mock_pre,
            patch("hypergumbo_tracker.sync.do_sync") as mock_sync,
        ):
            mock_pre.return_value = PreflightResult(
                ok=True,
                repo_root=tmp_path,
                git_dir=tmp_path / ".git",
                original_branch="dev",
                changed_files=[".agent/tracker/.ops/.WI-test.ops"],
                api_base="https://codeberg.org/api/v1/repos/o/r",
                forgejo_user="user",
                forgejo_token="tok",
            )
            mock_sync.return_value = SyncResult(
                success=True,
                pr_number=42,
                pr_url="https://codeberg.org/o/r/pulls/42",
                files_synced=1,
                exit_code=0,
            )
            with pytest.raises(SystemExit) as exc_info:
                main(["sync"])

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "synced 1 file(s)" in captured.out

    @patch("subprocess.run")
    def test_sync_failure(
        self,
        mock_subproc: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_tracker.cli import main

        mock_subproc.return_value = _make_completed_process(
            stdout=str(tmp_path)
        )

        with (
            patch("hypergumbo_tracker.sync.preflight_check") as mock_pre,
            patch("hypergumbo_tracker.sync.do_sync") as mock_sync,
        ):
            mock_pre.return_value = PreflightResult(
                ok=True,
                repo_root=tmp_path,
                git_dir=tmp_path / ".git",
                original_branch="dev",
                changed_files=[".agent/tracker/.ops/.WI-test.ops"],
                api_base="https://codeberg.org/api/v1/repos/o/r",
                forgejo_user="user",
                forgejo_token="tok",
            )
            mock_sync.return_value = SyncResult(
                success=False,
                error="CI failed",
                exit_code=1,
            )
            with pytest.raises(SystemExit) as exc_info:
                main(["sync"])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "CI failed" in captured.err

    @patch("subprocess.run")
    def test_sync_with_custom_args(
        self,
        mock_subproc: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Verify --base-branch and --timeout are passed through."""
        from hypergumbo_tracker.cli import main

        mock_subproc.return_value = _make_completed_process(
            stdout=str(tmp_path)
        )

        with (
            patch("hypergumbo_tracker.sync.preflight_check") as mock_pre,
            patch("hypergumbo_tracker.sync.do_sync") as mock_sync,
        ):
            mock_pre.return_value = PreflightResult(
                ok=True,
                repo_root=tmp_path,
                git_dir=tmp_path / ".git",
                original_branch="dev",
                changed_files=[".agent/tracker/.ops/.WI-test.ops"],
                api_base="https://codeberg.org/api/v1/repos/o/r",
                forgejo_user="user",
                forgejo_token="tok",
            )
            mock_sync.return_value = SyncResult(
                success=True, pr_number=1, files_synced=1, exit_code=0
            )
            with pytest.raises(SystemExit):
                main(
                    ["sync", "--base-branch", "main", "--timeout", "600"]
                )

            # Verify args passed to do_sync
            call_kwargs = mock_sync.call_args
            assert call_kwargs.kwargs["base_branch"] == "main"
            assert call_kwargs.kwargs["ci_timeout"] == 600


# ---------------------------------------------------------------------------
# TestSumAddedLines
# ---------------------------------------------------------------------------


class TestSumAddedLines:
    """Tests for _sum_added_lines — git diff --numstat parser."""

    def test_normal_output(self) -> None:
        output = "10\t5\t.agent/tracker/.ops/.WI-a.ops\n3\t0\t.agent/tracker/.ops/.WI-b.ops\n"
        assert _sum_added_lines(output) == 13

    def test_empty_output(self) -> None:
        assert _sum_added_lines("") == 0

    def test_binary_file_skipped(self) -> None:
        output = "-\t-\tbinary_file.bin\n5\t2\tnormal.txt\n"
        assert _sum_added_lines(output) == 5

    def test_malformed_input(self) -> None:
        output = "not a number\t0\tfile.txt\nok\n"
        assert _sum_added_lines(output) == 0

    def test_short_lines_ignored(self) -> None:
        output = "incomplete\n5\t2\tgood.txt\n"
        assert _sum_added_lines(output) == 5


# ---------------------------------------------------------------------------
# TestPendingSyncLines
# ---------------------------------------------------------------------------


class TestPendingSyncLines:
    """Tests for pending_sync_lines — counts pending tracker ops lines.

    The function first tries ``rev-parse --verify origin/dev`` to determine
    the diff base.  If origin/dev exists, it diffs against that (preventing
    re-sync of already-merged ops).  If not, it falls back to HEAD.
    All existing tests simulate origin/dev being available (the common case).
    """

    # Helper: rev-parse origin/dev succeeds (common case)
    _REV_PARSE_OK = _make_completed_process(stdout="abc123\n")

    # Helper: rev-parse origin/dev fails (fallback case)
    _REV_PARSE_FAIL = _make_completed_process(returncode=1)

    @patch("hypergumbo_tracker.sync._git")
    def test_with_changes(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        mock_git.side_effect = [
            self._REV_PARSE_OK,  # rev-parse origin/dev
            _make_completed_process(
                stdout="10\t2\t.agent/tracker/.ops/.WI-a.ops\n"
            ),  # diff origin/dev --numstat
            _make_completed_process(stdout=""),  # ls-files
        ]
        assert pending_sync_lines(tmp_path) == 10

    @patch("hypergumbo_tracker.sync._git")
    def test_no_changes(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        mock_git.side_effect = [
            self._REV_PARSE_OK,  # rev-parse origin/dev
            _make_completed_process(stdout=""),  # diff origin/dev
            _make_completed_process(stdout=""),  # ls-files
        ]
        assert pending_sync_lines(tmp_path) == 0

    @patch("hypergumbo_tracker.sync._git")
    def test_git_failure(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        mock_git.side_effect = [
            self._REV_PARSE_OK,  # rev-parse origin/dev
            _make_completed_process(returncode=1),  # diff fails
            _make_completed_process(returncode=1),  # ls-files fails
        ]
        assert pending_sync_lines(tmp_path) == 0

    @patch("hypergumbo_tracker.sync._git")
    def test_untracked_files(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        # Create an untracked ops file
        ops_dir = tmp_path / ".agent" / "tracker" / ".ops"
        ops_dir.mkdir(parents=True)
        ops_file = ops_dir / ".WI-new.ops"
        ops_file.write_text("line1\nline2\nline3\n")

        mock_git.side_effect = [
            self._REV_PARSE_OK,  # rev-parse origin/dev
            _make_completed_process(stdout=""),  # diff (no tracked changes)
            _make_completed_process(
                stdout=".agent/tracker/.ops/.WI-new.ops\n"
            ),  # ls-files
        ]
        assert pending_sync_lines(tmp_path) == 3

    @patch("hypergumbo_tracker.sync._git")
    def test_combined_tracked_and_untracked(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        # Create an untracked file
        ops_dir = tmp_path / ".agent" / "tracker-workspace" / ".ops"
        ops_dir.mkdir(parents=True)
        (ops_dir / ".WI-u.ops").write_text("a\nb\n")

        mock_git.side_effect = [
            self._REV_PARSE_OK,  # rev-parse origin/dev
            _make_completed_process(
                stdout="5\t0\t.agent/tracker/.ops/.WI-t.ops\n"
            ),  # tracked changes
            _make_completed_process(
                stdout=".agent/tracker-workspace/.ops/.WI-u.ops\n"
            ),  # untracked
        ]
        assert pending_sync_lines(tmp_path) == 7

    @patch("hypergumbo_tracker.sync._git")
    def test_untracked_file_missing(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        """ls-files lists a file that doesn't exist on disk (race condition)."""
        mock_git.side_effect = [
            self._REV_PARSE_OK,  # rev-parse origin/dev
            _make_completed_process(stdout=""),
            _make_completed_process(
                stdout=".agent/tracker/.ops/.WI-gone.ops\n"
            ),
        ]
        # File doesn't exist, so is_file() returns False; counted as 0
        assert pending_sync_lines(tmp_path) == 0

    @patch("hypergumbo_tracker.sync._git")
    def test_untracked_file_oserror(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        """OSError reading an untracked file is silently skipped."""
        ops_dir = tmp_path / ".agent" / "tracker" / ".ops"
        ops_dir.mkdir(parents=True)
        ops_file = ops_dir / ".WI-err.ops"
        ops_file.write_text("line1\n")

        mock_git.side_effect = [
            self._REV_PARSE_OK,  # rev-parse origin/dev
            _make_completed_process(stdout=""),  # diff
            _make_completed_process(
                stdout=".agent/tracker/.ops/.WI-err.ops\n"
            ),  # ls-files
        ]

        original_read = Path.read_text

        def failing_read(self_path: Path, *a: Any, **kw: Any) -> str:
            if self_path.name == ".WI-err.ops":
                raise OSError("permission denied")
            return original_read(self_path, *a, **kw)

        with patch.object(Path, "read_text", failing_read):
            result = pending_sync_lines(tmp_path)
        assert result == 0

    @patch("hypergumbo_tracker.sync._git")
    def test_diffs_against_origin_dev_not_head(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        """pending_sync_lines diffs against origin/dev, not HEAD.

        After do_sync merges ops to origin/dev, the ops are still dirty
        relative to the feature branch HEAD.  Diffing against origin/dev
        correctly shows 0 pending lines, preventing re-sync of already-synced
        ops.  This is the root cause of the duplicate PR bug: 4 identical
        tracker sync PRs created because the same ops kept triggering sync.
        """
        mock_git.side_effect = [
            self._REV_PARSE_OK,  # rev-parse origin/dev
            _make_completed_process(stdout=""),  # diff origin/dev (clean)
            _make_completed_process(stdout=""),  # ls-files
        ]
        assert pending_sync_lines(tmp_path) == 0

        # Verify the diff was against origin/dev, not HEAD
        diff_call = mock_git.call_args_list[1]
        assert "origin/dev" in diff_call.args[1:]
        assert "HEAD" not in diff_call.args[1:]

    @patch("hypergumbo_tracker.sync._git")
    def test_falls_back_to_head_when_origin_dev_missing(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        """Falls back to HEAD when origin/dev ref doesn't exist.

        In a fresh clone or disconnected state, origin/dev may not exist.
        The function should gracefully fall back to diffing against HEAD.
        """
        mock_git.side_effect = [
            self._REV_PARSE_FAIL,  # rev-parse origin/dev fails
            _make_completed_process(
                stdout="5\t0\t.agent/tracker/.ops/.WI-a.ops\n"
            ),  # diff HEAD --numstat
            _make_completed_process(stdout=""),  # ls-files
        ]
        assert pending_sync_lines(tmp_path) == 5

        # Verify fallback used HEAD
        diff_call = mock_git.call_args_list[1]
        assert "HEAD" in diff_call.args[1:]


# ---------------------------------------------------------------------------
# TestSyncSetupCheck
# ---------------------------------------------------------------------------


class TestSyncSetupCheck:
    """Tests for _check_sync_prerequisites — setup wizard check #21."""

    def test_no_repo_root(self, tmp_path: Path) -> None:
        from hypergumbo_tracker.setup import _check_sync_prerequisites

        result = _check_sync_prerequisites(tmp_path, repo_root=None)
        assert result.status == "ok"
        assert "skipped" in result.message.lower()

    @patch("hypergumbo_tracker.setup._find_git_dir")
    def test_no_remote(
        self, mock_find_git: MagicMock, tmp_path: Path
    ) -> None:
        from hypergumbo_tracker.setup import _check_sync_prerequisites

        mock_find_git.return_value = tmp_path / ".git"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _make_completed_process(returncode=1)
            result = _check_sync_prerequisites(
                tmp_path, repo_root=tmp_path
            )

        assert result.status == "warn"
        assert "remote" in result.message.lower()

    @patch("hypergumbo_tracker.setup._find_git_dir")
    def test_no_token(
        self,
        mock_find_git: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hypergumbo_tracker.setup import _check_sync_prerequisites

        mock_find_git.return_value = tmp_path / ".git"
        monkeypatch.delenv("FORGEJO_TOKEN", raising=False)

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_completed_process(
                    stdout="https://codeberg.org/o/r.git"
                ),  # remote
                _make_completed_process(stdout="User\n"),  # user.name
                _make_completed_process(stdout="u@e.com\n"),  # user.email
            ]
            result = _check_sync_prerequisites(
                tmp_path, repo_root=tmp_path
            )

        assert result.status == "warn"
        assert "FORGEJO_TOKEN" in result.message

    @patch("hypergumbo_tracker.setup._find_git_dir")
    def test_no_git_identity(
        self,
        mock_find_git: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hypergumbo_tracker.setup import _check_sync_prerequisites

        mock_find_git.return_value = tmp_path / ".git"
        monkeypatch.setenv("FORGEJO_TOKEN", "tok")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_completed_process(
                    stdout="https://codeberg.org/o/r.git"
                ),  # remote
                _make_completed_process(stdout=""),  # user.name (empty)
                _make_completed_process(stdout=""),  # user.email (empty)
            ]
            result = _check_sync_prerequisites(
                tmp_path, repo_root=tmp_path
            )

        assert result.status == "warn"
        assert "identity" in result.message.lower()

    @patch("hypergumbo_tracker.setup._find_git_dir")
    def test_all_ok(
        self,
        mock_find_git: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from hypergumbo_tracker.setup import _check_sync_prerequisites

        mock_find_git.return_value = tmp_path / ".git"
        monkeypatch.setenv("FORGEJO_TOKEN", "tok")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_completed_process(
                    stdout="https://codeberg.org/o/r.git"
                ),  # remote
                _make_completed_process(stdout="User\n"),  # user.name
                _make_completed_process(stdout="u@e.com\n"),  # user.email
            ]
            result = _check_sync_prerequisites(
                tmp_path, repo_root=tmp_path
            )

        assert result.status == "ok"

    @patch("hypergumbo_tracker.setup._find_git_dir")
    def test_token_in_env_file(
        self,
        mock_find_git: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Token in .env file (not os.environ) is detected."""
        from hypergumbo_tracker.setup import _check_sync_prerequisites

        mock_find_git.return_value = tmp_path / ".git"
        monkeypatch.delenv("FORGEJO_TOKEN", raising=False)

        # Write a .env file in the repo root
        (tmp_path / ".env").write_text("FORGEJO_TOKEN=tok_from_env\n")

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                _make_completed_process(
                    stdout="https://codeberg.org/o/r.git"
                ),  # remote
                _make_completed_process(stdout="User\n"),  # user.name
                _make_completed_process(stdout="u@e.com\n"),  # user.email
            ]
            result = _check_sync_prerequisites(
                tmp_path, repo_root=tmp_path
            )

        assert result.status == "ok"


# ---------------------------------------------------------------------------
# Failover detection
# ---------------------------------------------------------------------------


class TestDetectFailover:
    """Tests for _detect_failover — CI failover flag file parsing."""

    def test_no_flag_file(self, tmp_path: Path) -> None:
        """Returns inactive state when no flag file exists."""
        result = _detect_failover(tmp_path, {})
        assert result.active is False
        assert result.push_remote == "origin"

    def test_active_failover(self, tmp_path: Path) -> None:
        """Parses flag file and returns override state."""
        flag = tmp_path / "CI_FAILOVER_ACTIVE"
        flag.write_text(json.dumps({
            "selfhosted_forgejo_url": "http://10.85.0.10:3000",
            "selfhosted_forgejo_repo": "admin-josh/hypergumbo",
        }))
        env_vars = {
            "SELFHOSTED_FORGEJO_TOKEN": "local-tok",
            "SELFHOSTED_FORGEJO_USER": "local-user",
        }
        result = _detect_failover(tmp_path, env_vars)
        assert result.active is True
        assert result.api_base == (
            "http://10.85.0.10:3000/api/v1/repos/admin-josh/hypergumbo"
        )
        assert result.push_remote == "selfh"
        assert result.token == "local-tok"
        assert result.user == "local-user"

    def test_falls_back_to_forgejo_credentials(self, tmp_path: Path) -> None:
        """Falls back to FORGEJO_TOKEN/USER when LOCAL_ vars not set."""
        flag = tmp_path / "CI_FAILOVER_ACTIVE"
        flag.write_text(json.dumps({
            "selfhosted_forgejo_url": "http://10.85.0.10:3000",
            "selfhosted_forgejo_repo": "admin-josh/hypergumbo",
        }))
        env_vars = {"FORGEJO_TOKEN": "cb-tok", "FORGEJO_USER": "cb-user"}
        result = _detect_failover(tmp_path, env_vars)
        assert result.active is True
        assert result.token == "cb-tok"
        assert result.user == "cb-user"

    def test_missing_url_returns_inactive(self, tmp_path: Path) -> None:
        """Returns inactive when flag file lacks selfhosted_forgejo_url."""
        flag = tmp_path / "CI_FAILOVER_ACTIVE"
        flag.write_text(json.dumps({"selfhosted_forgejo_repo": "a/b"}))
        result = _detect_failover(tmp_path, {})
        assert result.active is False

    def test_missing_repo_returns_inactive(self, tmp_path: Path) -> None:
        """Returns inactive when flag file lacks selfhosted_forgejo_repo."""
        flag = tmp_path / "CI_FAILOVER_ACTIVE"
        flag.write_text(json.dumps({"selfhosted_forgejo_url": "http://x"}))
        result = _detect_failover(tmp_path, {})
        assert result.active is False

    def test_invalid_json_returns_inactive(self, tmp_path: Path) -> None:
        """Returns inactive on malformed JSON."""
        flag = tmp_path / "CI_FAILOVER_ACTIVE"
        flag.write_text("not json")
        result = _detect_failover(tmp_path, {})
        assert result.active is False


# ---------------------------------------------------------------------------
# TestSyncLog — log file management and garbage collection
# ---------------------------------------------------------------------------


class TestParseLogDate:
    """Tests for _parse_log_date — filename validation and date extraction."""

    def test_valid_filename(self) -> None:
        dt = _parse_log_date("sync-2026-03-15.log")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 15
        assert dt.tzinfo == timezone.utc

    def test_invalid_date_returns_none(self) -> None:
        """Feb 30 doesn't exist — should return None, not raise."""
        assert _parse_log_date("sync-2026-02-30.log") is None

    def test_wrong_prefix(self) -> None:
        assert _parse_log_date("tracker-2026-03-15.log") is None

    def test_wrong_extension(self) -> None:
        assert _parse_log_date("sync-2026-03-15.txt") is None

    def test_no_date(self) -> None:
        assert _parse_log_date("sync-.log") is None

    def test_partial_date(self) -> None:
        assert _parse_log_date("sync-2026-03.log") is None

    def test_empty_string(self) -> None:
        assert _parse_log_date("") is None

    def test_extra_suffix(self) -> None:
        """Extra characters after .log should not match."""
        assert _parse_log_date("sync-2026-03-15.log.bak") is None


class TestGcOldLogs:
    """Tests for gc_old_logs — safe 30-day log rotation."""

    def test_deletes_old_logs(self, tmp_path: Path) -> None:
        """Files older than RETENTION_DAYS are deleted."""
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        old_date = now - timedelta(days=RETENTION_DAYS + 1)
        old_name = f"sync-{old_date.strftime('%Y-%m-%d')}.log"
        (tmp_path / old_name).write_text("old data")

        deleted = gc_old_logs(tmp_path, now=now)
        assert old_name in deleted
        assert not (tmp_path / old_name).exists()

    def test_keeps_recent_logs(self, tmp_path: Path) -> None:
        """Files within RETENTION_DAYS are kept."""
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        recent_name = "sync-2026-03-14.log"
        (tmp_path / recent_name).write_text("recent data")

        deleted = gc_old_logs(tmp_path, now=now)
        assert deleted == []
        assert (tmp_path / recent_name).exists()

    def test_keeps_exactly_at_cutoff(self, tmp_path: Path) -> None:
        """Files exactly RETENTION_DAYS old are kept (cutoff is exclusive)."""
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        cutoff_date = now - timedelta(days=RETENTION_DAYS)
        cutoff_name = f"sync-{cutoff_date.strftime('%Y-%m-%d')}.log"
        (tmp_path / cutoff_name).write_text("boundary data")

        deleted = gc_old_logs(tmp_path, now=now)
        assert deleted == []
        assert (tmp_path / cutoff_name).exists()

    def test_ignores_non_log_files(self, tmp_path: Path) -> None:
        """Files not matching the pattern are never deleted."""
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        # Create a very old non-log file
        (tmp_path / "important-data.txt").write_text("keep me")
        (tmp_path / "sync-not-a-date.log").write_text("weird name")
        # Also create an old log to prove GC runs
        old_date = now - timedelta(days=RETENTION_DAYS + 5)
        old_name = f"sync-{old_date.strftime('%Y-%m-%d')}.log"
        (tmp_path / old_name).write_text("old")

        deleted = gc_old_logs(tmp_path, now=now)
        assert old_name in deleted
        assert (tmp_path / "important-data.txt").exists()
        assert (tmp_path / "sync-not-a-date.log").exists()

    def test_ignores_directories(self, tmp_path: Path) -> None:
        """Subdirectories are never deleted, even if name matches."""
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        old_date = now - timedelta(days=RETENTION_DAYS + 1)
        dir_name = f"sync-{old_date.strftime('%Y-%m-%d')}.log"
        (tmp_path / dir_name).mkdir()

        deleted = gc_old_logs(tmp_path, now=now)
        assert deleted == []
        assert (tmp_path / dir_name).is_dir()

    def test_handles_nonexistent_dir(self) -> None:
        """Returns empty list for nonexistent directory."""
        deleted = gc_old_logs(Path("/nonexistent/dir"))
        assert deleted == []

    def test_handles_unlink_permission_error(self, tmp_path: Path) -> None:
        """OSError on unlink is caught and skipped."""
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        old_date = now - timedelta(days=RETENTION_DAYS + 1)
        old_name = f"sync-{old_date.strftime('%Y-%m-%d')}.log"
        old_path = tmp_path / old_name
        old_path.write_text("old data")

        with patch.object(Path, "unlink", side_effect=OSError("perm")):
            deleted = gc_old_logs(tmp_path, now=now)

        # unlink failed — file still exists, not in deleted list
        assert deleted == []
        assert old_path.exists()

    def test_deletes_multiple_old_keeps_multiple_recent(
        self, tmp_path: Path,
    ) -> None:
        """Correctly handles a mix of old and recent files."""
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        old_names = []
        for days_ago in [35, 40, 60]:
            dt = now - timedelta(days=days_ago)
            name = f"sync-{dt.strftime('%Y-%m-%d')}.log"
            (tmp_path / name).write_text("old")
            old_names.append(name)

        recent_names = []
        for days_ago in [0, 5, 29]:
            dt = now - timedelta(days=days_ago)
            name = f"sync-{dt.strftime('%Y-%m-%d')}.log"
            (tmp_path / name).write_text("recent")
            recent_names.append(name)

        deleted = gc_old_logs(tmp_path, now=now)
        assert sorted(deleted) == sorted(old_names)
        for name in recent_names:
            assert (tmp_path / name).exists()

    def test_returns_sorted_list(self, tmp_path: Path) -> None:
        """Deleted filenames are returned in sorted order."""
        now = datetime(2026, 3, 15, tzinfo=timezone.utc)
        names = []
        for days_ago in [45, 35, 55]:
            dt = now - timedelta(days=days_ago)
            name = f"sync-{dt.strftime('%Y-%m-%d')}.log"
            (tmp_path / name).write_text("old")
            names.append(name)

        deleted = gc_old_logs(tmp_path, now=now)
        assert deleted == sorted(deleted)


class TestInitSyncLog:
    """Tests for init_sync_log — directory creation and log file opening."""

    def test_creates_log_dir(self, tmp_path: Path) -> None:
        log_dir = init_sync_log(tmp_path)
        assert log_dir is not None
        assert log_dir.is_dir()
        assert log_dir == tmp_path / ".agent" / ".sync-logs"

    def test_creates_daily_log_file(self, tmp_path: Path) -> None:
        import time as time_mod

        init_sync_log(tmp_path)
        today = time_mod.strftime("%Y-%m-%d")
        log_file = tmp_path / ".agent" / ".sync-logs" / f"sync-{today}.log"
        # File exists (may be empty since we haven't written yet)
        assert log_file.exists()

    def test_returns_none_on_readonly_filesystem(
        self, tmp_path: Path,
    ) -> None:
        with patch.object(Path, "mkdir", side_effect=OSError("read-only")):
            result = init_sync_log(tmp_path)
        assert result is None

    def test_runs_gc_on_init(self, tmp_path: Path) -> None:
        """Garbage collection runs during initialization."""
        log_dir = tmp_path / ".agent" / ".sync-logs"
        log_dir.mkdir(parents=True)
        # Create a very old log file
        (log_dir / "sync-2020-01-01.log").write_text("ancient")

        init_sync_log(tmp_path)
        assert not (log_dir / "sync-2020-01-01.log").exists()


    def test_reentrant_init_reuses_handle(self, tmp_path: Path) -> None:
        """Calling init_sync_log twice reuses the existing handle."""
        import hypergumbo_tracker.sync_log as sl

        result1 = init_sync_log(tmp_path)
        handle1 = sl._log_file_handle
        result2 = init_sync_log(tmp_path)
        handle2 = sl._log_file_handle
        assert result1 == result2
        assert handle1 is handle2  # Same handle, not reopened

    def test_open_failure_returns_none(self, tmp_path: Path) -> None:
        """If open() fails, init returns None and handle stays None."""
        import hypergumbo_tracker.sync_log as sl

        # Reset module state
        sl._log_file_handle = None
        log_dir = tmp_path / ".agent" / ".sync-logs"
        log_dir.mkdir(parents=True)

        with patch("builtins.open", side_effect=OSError("disk full")):
            result = init_sync_log(tmp_path)
        assert result is None
        assert sl._log_file_handle is None


class TestWriteLog:
    """Tests for write_log — dual output to stderr and file."""

    def test_writes_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        write_log("test message")
        captured = capsys.readouterr()
        assert "sync: test message" in captured.err

    def test_writes_to_file_when_initialized(
        self, tmp_path: Path,
    ) -> None:
        import time as time_mod

        init_sync_log(tmp_path)
        write_log("hello from test")

        today = time_mod.strftime("%Y-%m-%d")
        log_file = tmp_path / ".agent" / ".sync-logs" / f"sync-{today}.log"
        content = log_file.read_text()
        assert "sync: hello from test" in content
        assert "] sync: hello from test" in content  # has timestamp prefix

    def test_write_oserror_is_caught(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """OSError during file write doesn't crash; stderr still works."""
        import hypergumbo_tracker.sync_log as sl

        init_sync_log(tmp_path)
        # Sabotage the handle to raise on write
        mock_handle = MagicMock()
        mock_handle.closed = False
        mock_handle.write.side_effect = OSError("disk full")
        sl._log_file_handle = mock_handle

        write_log("should not crash")
        captured = capsys.readouterr()
        assert "sync: should not crash" in captured.err


class TestCloseLogAndGetLogDir:
    """Tests for _close_log and get_log_dir."""

    def test_close_log_closes_handle(self, tmp_path: Path) -> None:
        import hypergumbo_tracker.sync_log as sl

        init_sync_log(tmp_path)
        assert sl._log_file_handle is not None
        assert not sl._log_file_handle.closed

        sl._close_log()
        assert sl._log_file_handle is None

    def test_close_log_handles_oserror(self) -> None:
        """OSError on close doesn't crash."""
        import hypergumbo_tracker.sync_log as sl

        mock_handle = MagicMock()
        mock_handle.close.side_effect = OSError("IO error")
        sl._log_file_handle = mock_handle

        sl._close_log()  # Should not raise
        assert sl._log_file_handle is None

    def test_close_log_noop_when_none(self) -> None:
        import hypergumbo_tracker.sync_log as sl

        sl._log_file_handle = None
        sl._close_log()  # Should not raise
        assert sl._log_file_handle is None

    def test_get_log_dir_returns_path(self, tmp_path: Path) -> None:
        init_sync_log(tmp_path)
        from hypergumbo_tracker.sync_log import get_log_dir

        result = get_log_dir()
        assert result == tmp_path / ".agent" / ".sync-logs"

    def test_get_log_dir_returns_none_before_init(self) -> None:
        import hypergumbo_tracker.sync_log as sl

        sl._log_dir = None
        assert sl.get_log_dir() is None


# ---------------------------------------------------------------------------
# TestDoSync stale-pending retry paths
# ---------------------------------------------------------------------------


class TestDoSyncStalePending:
    """Tests for stale-pending detection and retry in do_sync."""

    @pytest.fixture(autouse=True)
    def _mock_init_sync_log(self) -> Any:
        with patch("hypergumbo_tracker.sync.init_sync_log"):
            yield

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._close_pr")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_stale_pending_retry_then_success(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_close: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """First CI poll returns stale_pending, retry succeeds."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *TestDoSync._plumbing_setup(),  # 9 plumbing calls
            _make_completed_process(),       # initial push (1 attempt)
            # stale_pending retry: close PR, repush
            _make_completed_process(),       # repush
            # success: rebase check + cleanup
            *TestDoSync._rebase_check_no_diverge(),  # 2 calls
            *TestDoSync._cleanup(),                   # 5 calls
        ]
        # First find_open_pr for initial, second for after repush
        mock_find_pr.side_effect = [(42, "sha123"), (43, "sha456")]
        # First poll: stale_pending; second: success
        mock_poll.side_effect = ["stale_pending", "success"]
        mock_close.return_value = True
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)

        assert result.success
        assert result.pr_number == 43
        mock_close.assert_called_once()
        assert mock_poll.call_count == 2

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._close_pr")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_stale_pending_all_retries_exhausted(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_close: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        """All retries return stale_pending → exit code 3."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *TestDoSync._plumbing_setup(),  # 9 plumbing calls
            _make_completed_process(),       # initial push
            # retry 1: repush
            _make_completed_process(),
            # retry 2: repush
            _make_completed_process(),
            # stale_pending exit: cleanup
            *TestDoSync._cleanup(),          # 5 calls
        ]
        mock_find_pr.side_effect = [
            (42, "sha1"), (43, "sha2"), (44, "sha3"),
        ]
        # 3 polls: initial + 2 retries, all stale_pending
        mock_poll.return_value = "stale_pending"
        mock_close.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)

        assert not result.success
        assert result.exit_code == 3
        assert "hung" in result.error.lower()
        assert mock_close.call_count == 2  # closed twice during retries
        assert mock_poll.call_count == 3   # initial + 2 retries


# ---------------------------------------------------------------------------
# TestDoSync cleanup failure paths
# ---------------------------------------------------------------------------


class TestDoSyncCleanupFailures:
    """Tests for do_sync cleanup failure modes.

    Verifies that the finally block handles errors gracefully: checkout
    failures, unlink OSErrors, and ff-only merge failures should all be
    logged but not crash the sync.
    """

    @staticmethod
    def _plumbing_setup() -> list[Any]:
        """Return git mock side_effect entries for the plumbing setup."""
        return [
            _make_completed_process(),                       # fetch
            _make_completed_process(stdout="abc123\n"),       # rev-parse
            _make_completed_process(),                       # read-tree
            _make_completed_process(),                       # add
            _make_completed_process(stdout="tree456\n"),      # write-tree
            _make_completed_process(stdout="Test User\n"),    # config user.name
            _make_completed_process(stdout="t@e.com\n"),      # config user.email
            _make_completed_process(stdout="commit789\n"),    # commit-tree
            _make_completed_process(),                       # update-ref
        ]

    @staticmethod
    def _rebase_check_no_diverge() -> list[Any]:
        return [
            _make_completed_process(),                       # fetch
            _make_completed_process(stdout="abc123\n"),       # rev-parse
        ]

    @patch("hypergumbo_tracker.sync.init_sync_log")
    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_checkout_failure_logs_and_continues(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        mock_init_log: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If checkout HEAD fails during cleanup, it's logged but sync
        still reports success (PR was already merged on remote)."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_no_diverge(),
            # Cleanup:
            _make_completed_process(),  # fetch
            _make_completed_process(
                returncode=1, stderr="error: pathspec"
            ),  # checkout HEAD -- FAILS
            _make_completed_process(stdout=""),  # ls-files --others
            _make_completed_process(),  # merge --ff-only
            _make_completed_process(),  # branch -D
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success
        assert result.pr_number == 42

    @patch("hypergumbo_tracker.sync.init_sync_log")
    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_unlink_oserror_logs_and_continues(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        mock_init_log: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If unlink raises OSError, cleanup logs the error and
        continues to the ff-only merge."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        ops_file = ".agent/tracker/.ops/.WI-test.ops"
        pre = _make_preflight(tmp_path, changed_files=[ops_file])

        # Create the ops file so unlink is attempted
        ops_path = tmp_path / ops_file
        ops_path.parent.mkdir(parents=True, exist_ok=True)
        ops_path.write_text("ops data\n")

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_no_diverge(),
            # Cleanup:
            _make_completed_process(),  # fetch
            _make_completed_process(),  # checkout HEAD
            _make_completed_process(stdout=f"{ops_file}\n"),  # ls-files
            _make_completed_process(),  # merge --ff-only
            _make_completed_process(),  # branch -D
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        # Patch unlink to raise OSError
        original_unlink = Path.unlink

        def mock_unlink(self_path: Path, *a: Any, **kw: Any) -> None:
            if ".WI-test.ops" in str(self_path):
                raise OSError("permission denied")
            original_unlink(self_path, *a, **kw)

        with patch.object(Path, "unlink", mock_unlink):
            result = do_sync(repo_root=tmp_path, preflight=pre)

        # Sync still succeeds (PR merged on remote)
        assert result.success

        # File still exists (unlink was blocked)
        assert ops_path.exists()

    @patch("hypergumbo_tracker.sync.init_sync_log")
    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._merge_pr")
    @patch("hypergumbo_tracker.sync._poll_ci")
    @patch("hypergumbo_tracker.sync._find_open_pr")
    @patch("hypergumbo_tracker.sync._git")
    def test_ff_merge_failure_logs_and_continues(
        self,
        mock_git: MagicMock,
        mock_find_pr: MagicMock,
        mock_poll: MagicMock,
        mock_merge: MagicMock,
        mock_time: MagicMock,
        mock_init_log: MagicMock,
        tmp_path: Path,
    ) -> None:
        """If ff-only merge fails during cleanup, sync still reports
        success — the PR was already merged on origin/dev."""
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            *self._plumbing_setup(),
            _make_completed_process(),  # push
            *self._rebase_check_no_diverge(),
            # Cleanup:
            _make_completed_process(),  # fetch
            _make_completed_process(),  # checkout HEAD
            _make_completed_process(stdout=""),  # ls-files --others
            _make_completed_process(
                returncode=1,
                stderr="fatal: Not possible to fast-forward",
            ),  # merge --ff-only FAILS
            _make_completed_process(),  # branch -D
        ]
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success
        assert result.pr_number == 42
