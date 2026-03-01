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
from http.client import HTTPResponse
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from hypergumbo_tracker.sync import (
    PreflightResult,
    SyncResult,
    _api_call,
    _detect_api_base,
    _find_open_pr,
    _git,
    _load_env,
    _check_pr_merged,
    _log,
    _merge_pr,
    _poll_ci,
    _sum_added_lines,
    do_sync,
    pending_sync_lines,
    preflight_check,
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
        mock_time.monotonic.side_effect = [0, 10, 20, 30]
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
        mock_time.monotonic.side_effect = [0, 1]
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
        mock_time.monotonic.side_effect = [0, 1]
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
        # First check: within deadline; second check: past deadline
        mock_time.monotonic.side_effect = [0, 5, 301]
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
        # After 60+ seconds with one holdout, bypass succeeds
        mock_time.monotonic.side_effect = [0, 61, 62]
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
        mock_time.monotonic.side_effect = [0, 5, 301]
        mock_time.sleep = MagicMock()
        mock_api.return_value = (200, [])  # non-dict body
        result = _poll_ci(
            "https://api.example.com/repos/o/r",
            "token",
            "sha123",
            timeout=300,
        )
        assert result == "timeout"


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
    """Tests for do_sync — full workflow with all externals mocked."""

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

        # Git calls: checkout -b, add, commit, push, checkout, pull, branch -D
        mock_git.return_value = _make_completed_process()
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

        mock_git.return_value = _make_completed_process()
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "success"
        mock_merge.return_value = True

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert result.success

        # Find the commit call (3rd git call: checkout -b, add, commit)
        commit_call = mock_git.call_args_list[2]
        commit_args = commit_call[0]  # positional args
        # Should include -c commit.gpgSign=false before "commit"
        assert "-c" in commit_args
        assert "commit.gpgSign=false" in commit_args

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._git")
    def test_branch_creation_failure(
        self,
        mock_git: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        # checkout -b fails, then cleanup calls
        mock_git.side_effect = [
            _make_completed_process(
                returncode=1, stderr="branch exists"
            ),  # checkout -b
            _make_completed_process(),  # cleanup: checkout original
            _make_completed_process(),  # cleanup: pull
            _make_completed_process(),  # cleanup: branch -D
        ]

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert "branch" in result.error.lower()

    @patch("hypergumbo_tracker.sync.time")
    @patch("hypergumbo_tracker.sync._git")
    def test_commit_failure(
        self,
        mock_git: MagicMock,
        mock_time: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_time.strftime.return_value = "20260218-120000"
        mock_time.sleep = MagicMock()
        pre = _make_preflight(tmp_path)

        mock_git.side_effect = [
            _make_completed_process(),  # checkout -b
            _make_completed_process(),  # add
            _make_completed_process(
                returncode=1, stderr="nothing to commit"
            ),  # commit
            _make_completed_process(),  # cleanup: checkout
            _make_completed_process(),  # cleanup: pull
            _make_completed_process(),  # cleanup: branch -D
        ]

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert "commit failed" in result.error

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
            _make_completed_process(),  # checkout -b
            _make_completed_process(),  # add
            _make_completed_process(),  # commit
            # 3 push failures
            _make_completed_process(returncode=1),
            _make_completed_process(returncode=1),
            _make_completed_process(returncode=1),
            # cleanup
            _make_completed_process(),
            _make_completed_process(),
            _make_completed_process(),
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

        mock_git.return_value = _make_completed_process()
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

        mock_git.return_value = _make_completed_process()
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

        mock_git.return_value = _make_completed_process()
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

        mock_git.return_value = _make_completed_process()
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

        mock_git.return_value = _make_completed_process()
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

        mock_git.return_value = _make_completed_process()
        mock_find_pr.return_value = (42, "sha123")
        mock_poll.return_value = "failure"

        result = do_sync(repo_root=tmp_path, preflight=pre)
        assert not result.success
        assert not (tmp_path / ".git" / "TRACKER_SYNC_PENDING").exists()

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
                _make_completed_process(),  # checkout -b
                _make_completed_process(),  # add
                _make_completed_process(),  # commit
                _make_completed_process(returncode=1),  # push attempt 1 fails
                _make_completed_process(),  # push attempt 2 succeeds
                # cleanup
                _make_completed_process(),
                _make_completed_process(),
                _make_completed_process(),
            ]
            mock_find.return_value = (42, "sha123")
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
    """Tests for pending_sync_lines — counts pending tracker ops lines."""

    @patch("hypergumbo_tracker.sync._git")
    def test_with_changes(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        mock_git.side_effect = [
            _make_completed_process(
                stdout="10\t2\t.agent/tracker/.ops/.WI-a.ops\n"
            ),  # diff HEAD --numstat
            _make_completed_process(stdout=""),  # ls-files
        ]
        assert pending_sync_lines(tmp_path) == 10

    @patch("hypergumbo_tracker.sync._git")
    def test_no_changes(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        mock_git.side_effect = [
            _make_completed_process(stdout=""),  # diff HEAD
            _make_completed_process(stdout=""),  # ls-files
        ]
        assert pending_sync_lines(tmp_path) == 0

    @patch("hypergumbo_tracker.sync._git")
    def test_git_failure(
        self, mock_git: MagicMock, tmp_path: Path,
    ) -> None:
        mock_git.side_effect = [
            _make_completed_process(returncode=1),  # diff HEAD fails
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
            _make_completed_process(stdout=""),  # diff HEAD (no tracked changes)
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
            _make_completed_process(stdout=""),  # diff HEAD
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
