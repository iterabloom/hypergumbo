# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared Tier-B harness for GitHub-backend behavioral tests (PR-C2).

This is NOT a test module — the leading underscore keeps pytest from collecting
it (``python_files`` only matches ``test_*``/``*_test``/``BRANCHES_*``). It is
imported by the per-script github-arm test files
(``test_ci_debug_github.py``, ``test_list_my_prs_github.py``,
``test_contribute_github.py``, ``test_autopr_github_arm.py``).

Why a shared module rather than the repo's usual per-file harness copy: PR-C2
adds several github-arm test files that all need the *same* fake network
boundary, so centralizing it removes ~4× duplication and gives one place to
evolve the stubs. Bash contributes no Python coverage (Tier B), so nothing here
counts toward the 100% gate — these are pure behavioral subprocess assertions.

The network boundary is stubbed two ways, both placed on ``PATH``:

* a fake ``curl`` (Python) that records every invocation to ``$CURL_LOG`` and
  returns responses fixtured via ``$CURL_FIXTURES`` (a JSON list of
  ``{match: "GET /pulls", code, body}`` matched against ``"<METHOD> <url>"``);
  byte-compatible with the fixture format in ``test_forge_backend_github.py``.
* a fake ``gh`` (Python) for ``contribute`` — records argv to ``$GH_LOG`` and
  emits fixtured stdout/exit for ``pr create`` / ``pr list`` / ``pr view``.

``run_script`` runs a real script as a subprocess in a throwaway git repo whose
``origin`` (and optional ``upstream``) drive backend detection; ``run_lib``
sources the lib and runs a snippet (for lib-level functions like
``resolve_forge_token``).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "scripts" / "lib" / "forgejo-api.sh"
SCRIPTS = REPO_ROOT / "scripts"

# Fake ``curl``: parses -X/-o/-H/-d + the URL, logs the invocation, and returns
# a fixtured body/code. Mirrors test_forge_backend_github.py's fake so fixtures
# are interchangeable.
FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
method = "GET"
outfile = None
url = None
headers = []
data = None
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
    if a in ("-w", "--max-time"):
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
    with open(outfile, "w") as fh:
        fh.write(body)
sys.stdout.write(code)
'''

# Fake ``gh`` for contribute: logs argv; ``pr list`` prints $GH_PR_LIST,
# ``pr create`` prints $GH_PR_CREATE_URL (default a github.com pull URL) and
# exits $GH_EXIT, ``pr view`` prints $GH_PR_VIEW.
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
log = os.environ.get("GH_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps(argv) + "\n")
sub = " ".join(argv[:2])
if sub == "pr list":
    sys.stdout.write(os.environ.get("GH_PR_LIST", ""))
    sys.exit(0)
if sub == "pr create":
    sys.stdout.write(os.environ.get("GH_PR_CREATE_URL", "https://github.com/o/r/pull/7") + "\n")
    sys.exit(int(os.environ.get("GH_EXIT", "0")))
if sub == "pr view":
    sys.stdout.write(os.environ.get("GH_PR_VIEW", "{}"))
    sys.exit(0)
sys.exit(int(os.environ.get("GH_EXIT", "0")))
'''


def fake_repo(
    tmp_path: Path,
    origin: str,
    *,
    upstream: str | None = None,
    branch: str | None = None,
) -> Path:
    """A throwaway git repo. ``origin`` (and optional ``upstream``) drive backend
    detection; ``branch`` checks out a feature branch with one extra commit."""
    root = tmp_path / "repo"
    root.mkdir()

    def run(*a: str) -> None:
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)

    run("init", "-q", "-b", "dev")
    run("config", "user.email", "t@e.com")
    run("config", "user.name", "T")
    run("commit", "--allow-empty", "-m", "seed")
    run("remote", "add", "origin", origin)
    if upstream:
        run("remote", "add", "upstream", upstream)
    if branch:
        run("checkout", "-q", "-b", branch)
        run("commit", "--allow-empty", "-m", "work")
    return root


def fake_fork_repo(
    tmp_path: Path,
    *,
    upstream: str,
    branch: str = "feature",
    fork_owner: str = "myuser",
    fork_name: str = "hypergumbo",
) -> Path:
    """A contributor fork checkout for ``scripts/contribute``.

    ``origin`` is a *pushable* local bare repo (so the real ``git push`` in
    contribute succeeds offline) whose path ends in ``<fork_owner>/<fork_name>``
    → a predictable ``FORK_SLUG`` for assertions. ``upstream`` is a URL string
    that drives backend detection (github.com → gh arm, else curl arm). The
    checkout is on ``branch``, one commit ahead of ``dev``.
    """
    bare = tmp_path / fork_owner / (fork_name + ".git")
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)

    root = tmp_path / "repo"
    root.mkdir()

    def run(*a: str) -> None:
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)

    run("init", "-q", "-b", "dev")
    run("config", "user.email", "t@e.com")
    run("config", "user.name", "T")
    run("commit", "--allow-empty", "-m", "seed")
    run("remote", "add", "origin", str(bare))
    run("remote", "add", "upstream", upstream)
    run("push", "-q", "origin", "dev")
    run("checkout", "-q", "-b", branch)
    run("commit", "--allow-empty", "-m", "work")
    return root


def bindir_with_fakes(tmp_path: Path, *, gh: bool = False) -> Path:
    """A PATH-prependable dir holding the fake ``curl`` (+ optional ``gh``)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl = bindir / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    if gh:
        g = bindir / "gh"
        g.write_text(FAKE_GH)
        g.chmod(0o755)
    return bindir


def _base_env(bindir: Path | None, repo: Path, fixtures: list | None) -> tuple[dict, dict]:
    env = dict(os.environ)
    for k in (
        "FORGEJO_TOKEN",
        "SELFHOSTED_FORGEJO_TOKEN",
        "HYPERGUMBO_FORGE_BACKEND",
        "HG_GITHUB_TOKEN",
    ):
        env.pop(k, None)
    env["FORGEJO_TOKEN"] = "tok"
    logs: dict[str, Path] = {}
    if bindir is not None:
        env["PATH"] = f"{bindir}:{env['PATH']}"
        logs["curl"] = repo / "curl.log"
        env["CURL_LOG"] = str(logs["curl"])
        env["CURL_FIXTURES"] = json.dumps(fixtures or [])
        logs["gh"] = repo / "gh.log"
        env["GH_LOG"] = str(logs["gh"])
    return env, logs


def run_script(
    script: str | Path,
    repo: Path,
    argv: tuple[str, ...] = (),
    *,
    fixtures: list | None = None,
    env: dict | None = None,
    bindir: Path | None = None,
    timeout: float = 90,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Path]]:
    """Run ``scripts/<script>`` as a subprocess in ``repo``. Returns (result, logs)."""
    script_path = script if Path(script).is_absolute() else SCRIPTS / script
    full_env, logs = _base_env(bindir, repo, fixtures)
    if env:
        full_env.update(env)
    result = subprocess.run(
        ["bash", str(script_path), *argv],
        cwd=repo,
        capture_output=True,
        text=True,
        env=full_env,
        timeout=timeout,
    )
    return result, logs


def run_lib(
    repo: Path,
    snippet: str,
    *,
    fixtures: list | None = None,
    env: dict | None = None,
    bindir: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path | None]:
    """Source the lib in ``repo`` and run ``snippet``. Returns (result, curl_log)."""
    full_env, logs = _base_env(bindir, repo, fixtures)
    if env:
        full_env.update(env)
    script = f'source "{LIB}"\nload_env\n{snippet}\n'
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=repo,
        capture_output=True,
        text=True,
        env=full_env,
    )
    return result, logs.get("curl")


def calls(log: Path | None) -> list[dict]:
    """Parse a $CURL_LOG (or $GH_LOG) into a list of records."""
    if not log or not Path(log).exists():
        return []
    return [json.loads(ln) for ln in Path(log).read_text().splitlines() if ln.strip()]
