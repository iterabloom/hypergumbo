# SPDX-License-Identifier: AGPL-3.0-or-later
"""A forge token never appears on a command line (WI-jizos).

A process's argument list is public on the machine: ``ps``, ``pgrep -a`` and
``/proc/<pid>/cmdline`` show it to every local user for as long as the process
lives. The scripts handed git and curl their token exactly there::

    git -c credential.helper="!f() { echo username=$USER; echo password=$TOKEN; }; f" push ...
    curl -H "Authorization: token $TOKEN" ...

and a process listing taken while the tracker's auto-sync was pushing printed
the token into an agent's transcript. The credential now travels in the child
process's ENVIRONMENT (git) or on its STANDARD INPUT (curl), both readable
only by the owner: ``scripts/lib/secret-args.sh`` for the shell scripts,
``hypergumbo_tracker.sync._CREDENTIAL_HELPER`` for the tracker.

Three kinds of test, because each catches what the others cannot:

* BEHAVIOUR: the real functions are run against stand-ins for ``git`` and
  ``curl`` that record their own arguments. The token must be absent from the
  arguments AND must still arrive (the helper answers with it; curl receives
  the header). A fix that hid the token by dropping it would pass the first
  half alone.
* The credential helper is run the way git runs one, through ``sh -c``, with
  and without the environment, so "names only" is checked on the text git
  receives. The tracker's helper is tested the same way in its own package
  (``packages/hypergumbo-tracker/tests/test_sync_credential_helper.py``):
  this directory is also run with no hypergumbo package installed, so
  nothing here may import one.
* A SCAN of the scripts and of the tracker's sources for the two old
  spellings, so a new call site cannot bring them back. The scan is the only
  check that covers sites no test executes (``auto-pr``'s push paths need a
  forge).
"""
# covers: scripts/lib/secret-args.sh
# covers: scripts/lib/forgejo-api.sh
# covers: scripts/lib/github-api.sh
# covers: scripts/auto-pr
# covers: scripts/contribute
# covers: scripts/ci-debug
# covers: scripts/release
# covers: packages/hypergumbo-tracker/src/hypergumbo_tracker/sync.py
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import _forge_github_harness as H

REPO_ROOT = Path(__file__).resolve().parent.parent
SECRET_ARGS = REPO_ROOT / "scripts" / "lib" / "secret-args.sh"
#: Distinctive, so a substring search of recorded arguments cannot match by
#: accident (the harness's default token is the three letters ``tok``).
TOKEN = "s3cr3t-T0KEN-must-not-be-an-argument-0123456789"

# A stand-in git: records its arguments and the two credential variables it was
# given, then asks the helper it was handed for a credential the way git does
# (``sh -c "<helper> get"``) and records the answer.
FAKE_GIT = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
helper = next((a.split("=", 1)[1] for a in args if a.startswith("credential.helper=")), "")
answer = ""
if helper.startswith("!"):
    answer = subprocess.run(["sh", "-c", helper[1:] + " get"], capture_output=True, text=True).stdout
with open(os.environ["GIT_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": args, "answer": answer}) + "\n")
'''


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/o/r.git"],
        check=True,
    )
    return repo


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestGitWithToken:
    def _run(self, tmp_path: Path, snippet: str) -> list[dict]:
        bindir = tmp_path / "bin"
        bindir.mkdir()
        git = bindir / "git"
        git.write_text(FAKE_GIT)
        git.chmod(0o755)
        log = tmp_path / "git.log"
        env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "GIT_LOG": str(log)}
        result = subprocess.run(
            ["bash", "-c", f'source "{SECRET_ARGS}"\n{snippet}\n'],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, result.stderr
        return _records(log)

    def test_the_token_is_not_an_argument_and_still_reaches_git(self, tmp_path: Path) -> None:
        (call,) = self._run(
            tmp_path, f'git_with_token "x-access-token" "{TOKEN}" push origin HEAD:refs/heads/b',
        )
        assert call["argv"][-3:] == ["push", "origin", "HEAD:refs/heads/b"], call["argv"]
        assert not [a for a in call["argv"] if TOKEN in a], "the token is on git's command line"
        assert not [a for a in call["argv"] if "x-access-token" in a]
        # Reach: hidden is not the same as lost.
        assert call["answer"].splitlines() == ["username=x-access-token", f"password={TOKEN}"]

    def test_the_variables_do_not_outlive_the_call(self, tmp_path: Path) -> None:
        """They are set for the one git process, not exported into the script."""
        calls = self._run(
            tmp_path,
            f'git_with_token u "{TOKEN}" push origin x\n'
            'echo "after=[${HG_GIT_CRED_TOKEN:-}]" >&2\n'
            "git status",
        )
        assert calls[1]["argv"] == ["status"]
        result = subprocess.run(
            ["bash", "-c", f'source "{SECRET_ARGS}"\ngit_with_token u t version >/dev/null\n'
                           'echo "after=[${HG_GIT_CRED_TOKEN:-}]"'],
            capture_output=True, text=True,
        )
        assert "after=[]" in result.stdout, result.stdout + result.stderr

    def test_the_exit_status_is_gits(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f'source "{SECRET_ARGS}"\ngit_with_token u t no-such-subcommand'],
            capture_output=True, text=True,
        )
        assert result.returncode != 0


class TestCurlWithSecretHeaders:
    def test_the_api_wrapper_sends_the_header_and_keeps_it_off_the_command_line(
        self, tmp_path: Path,
    ) -> None:
        repo = _git_repo(tmp_path)
        bindir = H.bindir_with_fakes(tmp_path)
        result, log = H.run_lib(
            repo,
            'detect_api_base; api_get "$API_BASE/pulls/1" || true',
            fixtures=[{"match": "GET https://api.github.com", "code": 200, "body": "{}"}],
            bindir=bindir,
            env={"HG_GITHUB_TOKEN": TOKEN},
        )
        calls = _records(log)
        assert calls, result.stdout + result.stderr
        for call in calls:
            assert not [a for a in call["argv"] if TOKEN in a], "the token is on curl's command line"
        # Reach: the header arrived, with the token in it.
        assert f"Authorization: token {TOKEN}" in calls[-1]["headers"]
        assert "Accept: application/vnd.github+json" in calls[-1]["headers"]

    def test_several_secret_headers_all_arrive(self, tmp_path: Path) -> None:
        """The Woodpecker wrapper sends three; none may be an argument."""
        repo = _git_repo(tmp_path)
        bindir = H.bindir_with_fakes(tmp_path)
        result, log = H.run_lib(
            repo,
            '_wp_curl "https://ci.example/api/user"',
            bindir=bindir,
            env={"WOODPECKER_TOKEN": TOKEN, "CF_ACCESS_CLIENT_ID": "id-" + TOKEN,
                 "CF_ACCESS_CLIENT_SECRET": "cf-" + TOKEN},
        )
        (call,) = _records(log)
        assert not [a for a in call["argv"] if TOKEN in a], result.stderr
        assert call["headers"] == [
            f"Authorization: Bearer {TOKEN}",
            f"CF-Access-Client-Id: id-{TOKEN}",
            f"CF-Access-Client-Secret: cf-{TOKEN}",
        ]
        assert call["url"] == "https://ci.example/api/user"

    def test_real_curl_reads_headers_from_standard_input(self) -> None:
        """The stand-in is not the proof that ``-H @-`` exists: ask the curl on this machine.

        ``--version`` needs no network; a curl older than 7.55.0 would not
        read ``@-`` as "from standard input" and would send it as a header.
        """
        out = subprocess.run(["curl", "--version"], capture_output=True, text=True).stdout
        found = re.match(r"curl (\d+)\.(\d+)", out)
        assert found, out
        assert (int(found.group(1)), int(found.group(2))) >= (7, 55), out


# The two old spellings: a credential helper whose text expands a variable
# where it is WRITTEN (double quotes in shell, an f-string in Python), and an
# Authorization-style header given to curl as an argument.
_HELPER_WITH_A_VALUE = re.compile(r"""credential\.helper=(?:"[^"\n]*\$|\{?[^'\n]*password=\{)""")
_SECRET_HEADER_ARGUMENT = re.compile(
    r"""-H\s+["'][^"'\n]*(?:Authorization|Client-Secret|PRIVATE-TOKEN)[^"'\n]*\$""", re.IGNORECASE,
)


def _scanned_files() -> list[Path]:
    files = [p for p in (REPO_ROOT / "scripts").rglob("*") if p.is_file() and p.suffix in ("", ".sh", ".py")]
    files += list((REPO_ROOT / "packages" / "hypergumbo-tracker" / "src").rglob("*.py"))
    files += list((REPO_ROOT / ".githooks").rglob("*")) if (REPO_ROOT / ".githooks").is_dir() else []
    return [p for p in files if p.is_file() and "__pycache__" not in p.parts]


def _hits(pattern: re.Pattern[str]) -> list[str]:
    hits = []
    for path in _scanned_files():
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if pattern.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()[:120]}")
    return hits


class TestNoOldSpellingAnywhere:
    def test_the_scan_reads_the_files_it_is_about(self) -> None:
        names = {str(p.relative_to(REPO_ROOT)) for p in _scanned_files()}
        for needed in ("scripts/auto-pr", "scripts/contribute", "scripts/ci-debug", "scripts/release",
                       "scripts/lib/forgejo-api.sh", "scripts/lib/github-api.sh",
                       "packages/hypergumbo-tracker/src/hypergumbo_tracker/sync.py"):
            assert needed in names, needed

    def test_the_patterns_match_the_old_spellings(self) -> None:
        """Positive control: each pattern finds the line it exists to forbid."""
        assert _HELPER_WITH_A_VALUE.search(
            'git -c credential.helper="!f() { echo username=$USER; echo password=$TOKEN; }; f" push')
        assert _HELPER_WITH_A_VALUE.search(
            'f"credential.helper=!f() {{ echo password={preflight.forgejo_token}; }}; f"')
        assert _SECRET_HEADER_ARGUMENT.search('curl -s -H "Authorization: token $FORGEJO_TOKEN" url')
        assert _SECRET_HEADER_ARGUMENT.search('-H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \\')
        assert not _HELPER_WITH_A_VALUE.search(
            """git -c credential.helper='!f() { echo "password=$HG_GIT_CRED_TOKEN"; }; f' "$@" """)
        assert not _SECRET_HEADER_ARGUMENT.search('curl_args+=(-H "Accept: application/vnd.github+json")')

    def test_no_credential_helper_carries_a_value(self) -> None:
        assert _hits(_HELPER_WITH_A_VALUE) == []

    def test_no_secret_header_is_a_curl_argument(self) -> None:
        assert _hits(_SECRET_HEADER_ARGUMENT) == []
