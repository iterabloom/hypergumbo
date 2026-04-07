# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the ``merge-pr close`` subcommand (WI-vonis).

The close subcommand adds a sanctioned path to close a PR without
merging it, filling a coverage gap in the CI-interaction policy (see
``AGENTS.md`` §"CI Interaction Policy"). Before this, agents had to
either ask a human to click a button on the forge UI or violate the
policy by sourcing ``lib/forgejo-api.sh`` inline.

Strategy
--------
The real ``scripts/merge-pr`` script sources ``$SCRIPT_DIR/lib/forgejo-api.sh``
at startup. We build a temp dir with:

  - a copy of the real ``scripts/merge-pr``
  - a *stub* ``lib/forgejo-api.sh`` that records every ``api_get``,
    ``api_patch``, and ``api_post`` call into a log file and lets each
    test program the mock responses via a small JSON control file.

The tests then run ``bash <tempdir>/merge-pr close <args>`` in a
subprocess, assert on exit code + stdout, and inspect the recorded
call log. This exercises the real argument parser and control flow
while isolating the forge HTTP surface.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
MERGE_PR_PATH = REPO_ROOT / "scripts" / "merge-pr"


@pytest.fixture()
def stub_env(tmp_path: Path):
    """Build a temp dir with merge-pr + a stub forgejo-api.sh.

    Yields a helper object with:
      - ``run(*args)`` — run ``merge-pr`` with the given args, return
        ``subprocess.CompletedProcess``.
      - ``configure(**kwargs)`` — write the JSON control file that the
        stub reads to decide what to return from api_get/patch/post.
      - ``calls()`` — parse and return the recorded call log as a
        list of dicts.
    """
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    lib_dir = stub_dir / "lib"
    lib_dir.mkdir()
    call_log = tmp_path / "calls.log"
    control = tmp_path / "control.json"
    control.write_text("{}")

    stub_lib = lib_dir / "forgejo-api.sh"
    stub_lib.write_text(
        rf'''#!/usr/bin/env bash
# Stub forgejo-api.sh for WI-vonis tests.
# Records api_get/api_patch/api_post calls and returns programmed
# responses from a JSON control file.

API_BASE="https://forge.example/api/v1/repos/test/repo"
API_RESPONSE=""
API_HTTP_CODE="200"
FAILOVER_ACTIVE="false"
FAILOVER_URL=""
FAILOVER_REPO=""
REPO_ROOT="{tmp_path.as_posix()}"
REPO_SLUG="test/repo"

_CONTROL_FILE="{control.as_posix()}"
_CALL_LOG="{call_log.as_posix()}"

_control_get() {{
    python3 -c "
import json, sys
try:
    data = json.load(open('$_CONTROL_FILE'))
except Exception:
    data = {{}}
path = sys.argv[1].split('.')
for p in path:
    if isinstance(data, dict) and p in data:
        data = data[p]
    else:
        data = None
        break
if data is None:
    print('')
elif isinstance(data, (dict, list)):
    print(json.dumps(data))
else:
    print(data)
" "$1"
}}

_log_call() {{
    local method="$1"
    local url="$2"
    local body="${{3:-}}"
    python3 -c "
import json, sys
entry = {{'method': sys.argv[1], 'url': sys.argv[2], 'body': sys.argv[3]}}
with open('$_CALL_LOG', 'a') as f:
    f.write(json.dumps(entry) + '\n')
" "$method" "$url" "$body"
}}

load_env() {{ :; }}
detect_api_base() {{ :; }}
apply_failover_overrides() {{ :; }}

json_field() {{
    python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
path = sys.argv[1].split('.')
for p in path:
    if isinstance(data, dict) and p in data:
        data = data[p]
    else:
        sys.exit(0)
if isinstance(data, (dict, list)):
    print(json.dumps(data))
else:
    print(data)
" "$1"
}}

api_get() {{
    local url="$1"
    _log_call GET "$url" ""
    local response_key="get_response"
    local http_key="get_http_code"
    API_RESPONSE=$(_control_get "$response_key")
    local code
    code=$(_control_get "$http_key")
    API_HTTP_CODE="${{code:-200}}"
    [[ "$API_HTTP_CODE" == "200" ]]
}}

api_patch() {{
    local url="$1"
    local body="${{2:-}}"
    _log_call PATCH "$url" "$body"
    API_RESPONSE=$(_control_get "patch_response")
    local code
    code=$(_control_get "patch_http_code")
    API_HTTP_CODE="${{code:-200}}"
    [[ "$API_HTTP_CODE" == "200" ]]
}}

api_post() {{
    local url="$1"
    local body="${{2:-}}"
    _log_call POST "$url" "$body"
    API_RESPONSE=$(_control_get "post_response")
    local code
    code=$(_control_get "post_http_code")
    API_HTTP_CODE="${{code:-200}}"
    [[ "$API_HTTP_CODE" == "200" ]]
}}

# Unused-by-close stubs (satisfy merge-pr's imports)
poll_ci() {{ return 0; }}
do_merge() {{ return 0; }}
'''
    )

    # Copy the real merge-pr script into the stub dir so it sources
    # stub_lib via its SCRIPT_DIR lookup.
    shutil.copy2(MERGE_PR_PATH, stub_dir / "merge-pr")
    (stub_dir / "merge-pr").chmod(0o755)

    class Harness:
        def __init__(self):
            self.call_log = call_log
            self.control = control
            self.stub_dir = stub_dir

        def configure(self, **kwargs):
            """Program the stub's responses.

            Known keys: get_response, get_http_code, patch_response,
            patch_http_code, post_response, post_http_code.
            """
            self.control.write_text(json.dumps(kwargs))

        def run(self, *args, env=None):
            cmd_env = dict(os.environ)
            cmd_env["FORGEJO_TOKEN"] = "stub-token"
            if env:
                cmd_env.update(env)
            return subprocess.run(
                ["bash", str(self.stub_dir / "merge-pr"), *args],
                capture_output=True,
                text=True,
                env=cmd_env,
            )

        def calls(self):
            if not self.call_log.exists():
                return []
            return [
                json.loads(line)
                for line in self.call_log.read_text().splitlines()
                if line.strip()
            ]

    yield Harness()


class TestMergePrClose:
    """WI-vonis: ``merge-pr close <PR> [--reason TEXT]`` subcommand."""

    def test_happy_path_sends_patch(self, stub_env):
        """Closing an open PR sends PATCH state=closed and exits 0."""
        stub_env.configure(
            get_response=json.dumps(
                {"state": "open", "merged": False, "title": "x",
                 "body": "", "head": {"sha": "abc", "ref": "br"},
                 "base": {"ref": "dev"}}
            ),
        )
        result = stub_env.run("close", "2729")
        assert result.returncode == 0, result.stderr + result.stdout
        calls = stub_env.calls()
        # Must include a PATCH /pulls/2729 with state: closed
        patches = [c for c in calls if c["method"] == "PATCH"]
        assert len(patches) == 1
        assert "/pulls/2729" in patches[0]["url"]
        assert "closed" in patches[0]["body"]

    def test_refuse_already_merged(self, stub_env):
        """Closing an already-merged PR refuses with non-zero exit."""
        stub_env.configure(
            get_response=json.dumps(
                {"state": "closed", "merged": True, "title": "x",
                 "body": "", "head": {"sha": "abc", "ref": "br"},
                 "base": {"ref": "dev"}}
            ),
        )
        result = stub_env.run("close", "2729")
        assert result.returncode != 0
        # No PATCH should have been sent
        calls = stub_env.calls()
        patches = [c for c in calls if c["method"] == "PATCH"]
        assert patches == []

    def test_idempotent_already_closed(self, stub_env):
        """Closing an already-closed (non-merged) PR is a no-op."""
        stub_env.configure(
            get_response=json.dumps(
                {"state": "closed", "merged": False, "title": "x",
                 "body": "", "head": {"sha": "abc", "ref": "br"},
                 "base": {"ref": "dev"}}
            ),
        )
        result = stub_env.run("close", "2729")
        assert result.returncode == 0
        # No PATCH should have been sent (idempotent no-op)
        calls = stub_env.calls()
        patches = [c for c in calls if c["method"] == "PATCH"]
        assert patches == []

    def test_network_failure_on_fetch(self, stub_env):
        """A non-2xx on the initial fetch exits non-zero."""
        stub_env.configure(get_http_code="500")
        result = stub_env.run("close", "2729")
        assert result.returncode != 0

    def test_network_failure_on_patch(self, stub_env):
        """A non-2xx on the close PATCH exits non-zero."""
        stub_env.configure(
            get_response=json.dumps(
                {"state": "open", "merged": False, "title": "x",
                 "body": "", "head": {"sha": "abc", "ref": "br"},
                 "base": {"ref": "dev"}}
            ),
            patch_http_code="500",
        )
        result = stub_env.run("close", "2729")
        assert result.returncode != 0

    def test_reason_posts_comment_before_close(self, stub_env):
        """--reason posts a comment to issues/N/comments before the PATCH."""
        stub_env.configure(
            get_response=json.dumps(
                {"state": "open", "merged": False, "title": "x",
                 "body": "", "head": {"sha": "abc", "ref": "br"},
                 "base": {"ref": "dev"}}
            ),
        )
        result = stub_env.run(
            "close", "2729", "--reason", "superseded by PR #2730",
        )
        assert result.returncode == 0
        calls = stub_env.calls()
        post_calls = [c for c in calls if c["method"] == "POST"]
        patch_calls = [c for c in calls if c["method"] == "PATCH"]
        assert len(post_calls) == 1
        assert "/issues/2729/comments" in post_calls[0]["url"]
        assert "superseded" in post_calls[0]["body"]
        # POST (comment) must come before PATCH (close) in the log
        call_order = [c["method"] for c in calls]
        assert call_order.index("POST") < call_order.index("PATCH")
        assert len(patch_calls) == 1

    def test_reason_comment_failure_does_not_block_close(self, stub_env):
        """When the comment POST fails, the close PATCH still happens."""
        stub_env.configure(
            get_response=json.dumps(
                {"state": "open", "merged": False, "title": "x",
                 "body": "", "head": {"sha": "abc", "ref": "br"},
                 "base": {"ref": "dev"}}
            ),
            post_http_code="500",
        )
        result = stub_env.run(
            "close", "2729", "--reason", "x",
        )
        assert result.returncode == 0
        # PATCH still happened despite comment failing
        calls = stub_env.calls()
        patches = [c for c in calls if c["method"] == "PATCH"]
        assert len(patches) == 1

    def test_missing_pr_number(self, stub_env):
        """``merge-pr close`` with no PR number exits non-zero."""
        result = stub_env.run("close")
        assert result.returncode != 0

    def test_help_exits_zero(self, stub_env):
        """``merge-pr close --help`` exits zero and prints usage."""
        result = stub_env.run("close", "--help")
        assert result.returncode == 0
        assert "close" in result.stdout.lower()

    def test_close_does_not_require_wait_for_ci(self, stub_env):
        """The 'close' subcommand does not block on CI status."""
        # get_response indicates CI would be failing, but close should
        # proceed anyway — closing a PR is not gated on CI.
        stub_env.configure(
            get_response=json.dumps(
                {"state": "open", "merged": False, "title": "x",
                 "body": "", "head": {"sha": "abc", "ref": "br"},
                 "base": {"ref": "dev"}}
            ),
        )
        result = stub_env.run("close", "2729")
        assert result.returncode == 0
        # We never fetched the CI status endpoint
        calls = stub_env.calls()
        for c in calls:
            assert "/commits/" not in c["url"]
