# SPDX-License-Identifier: AGPL-3.0-or-later
"""``scripts/publish-scheduled-verdict``: a scheduled run's verdict reaches the badges branch.

The script exists because the GitHub Actions badge step it replaces swallowed
its own push failures (``|| echo "Push failed"``) and its badge stood frozen
for five months. So besides the verdict's content these tests pin the publish
itself against a local bare repository standing in for GitHub: the files land,
other files on the branch survive, four legs racing each other all land, and
a publish that cannot happen exits 1 with one line on stderr. The SSH path is
exercised with a stand-in ``ssh`` on PATH (GitHub itself is not contacted).
"""
# covers: scripts/publish-scheduled-verdict
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "publish-scheduled-verdict"
SHA = "0123456789abcdef0123456789abcdef01234567"
STEPS = ["prepare-git", "build-grammars", "test-all-packages"]
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _load() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("publish_scheduled_verdict", str(SCRIPT))
    spec = importlib.util.spec_from_loader("publish_scheduled_verdict", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["publish_scheduled_verdict"] = module
    loader.exec_module(module)
    return module


psv = _load()


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
        env={**os.environ, **GIT_ENV})
    return proc.stdout.strip()


def _bare(tmp_path: Path, name: str = "remote.git") -> Path:
    bare = tmp_path / name
    _git("init", "-q", "--bare", str(bare))
    # Like GitHub without branch protection: a forced push would be accepted,
    # so only the publisher's own refusal to force keeps another leg's commit.
    # (`git init` turns this on when core.sharedRepository is set globally.)
    _git("--git-dir", str(bare), "config", "receive.denyNonFastForwards", "false")
    return bare


def _seed_badges(tmp_path: Path, bare: Path, files: dict[str, str]) -> None:
    """Put ``files`` on the bare repo's badges branch, as June's branch has them."""
    work = tmp_path / "seed"
    _git("init", "-q", str(work))
    for name, text in files.items():
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(text)
    _git("add", ".", cwd=work)
    _git("commit", "-q", "-m", "seed", cwd=work)
    _git("push", "-q", str(bare), "HEAD:refs/heads/badges", cwd=work)


def _branch_file(bare: Path, path: str) -> str:
    return _git("--git-dir", str(bare), "show", f"badges:{path}")


def _branch_files(bare: Path) -> list[str]:
    return _git("--git-dir", str(bare), "ls-tree", "-r", "--name-only", "badges").splitlines()


def _coverage_report(path: Path, *, percent: float, statements: int,
                     missing: dict[str, list[int]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "totals": {"percent_covered": percent, "num_statements": statements,
                   "missing_lines": sum(len(v) for v in missing.values())},
        "files": {name: {"missing_lines": lines} for name, lines in missing.items()},
    }))
    return path


@pytest.fixture
def ci_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI_COMMIT_SHA", SHA)
    monkeypatch.setenv("CI_PIPELINE_NUMBER", "6480")
    monkeypatch.setenv("CI_PIPELINE_URL", "https://ci.example/repos/1/pipeline/6480")
    for var in ("BADGES_DEPLOY_KEY", "CI_REPO_CLONE_SSH_URL", "CI_REPO"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(psv, "PAUSE_SECONDS", (0.0, 0.0))


def _markers(tmp_path: Path, passed: list[str]) -> Path:
    markers = tmp_path / "markers"
    for step in passed:
        assert psv.main(["mark", step, "--markers", str(markers)]) == 0
    return markers


# ── mark ─────────────────────────────────────────────────────────────────────

def test_mark_defaults_to_the_git_dir_so_the_working_tree_stays_clean(tmp_path: Path) -> None:
    repo = tmp_path / "ws"
    _git("init", "-q", str(repo))
    proc = subprocess.run([str(SCRIPT), "mark", "build-grammars"], cwd=repo,
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert (repo / ".git" / "scheduled-verdict" / "build-grammars.passed").is_file()
    assert _git("status", "--porcelain", cwd=repo) == ""


def test_mark_outside_a_repository_fails_loudly(tmp_path: Path) -> None:
    proc = subprocess.run([str(SCRIPT), "mark", "x"], cwd=tmp_path, capture_output=True,
                          text=True, check=False, env={**os.environ, "GIT_CEILING_DIRECTORIES": str(tmp_path)})
    assert proc.returncode == 1
    assert "not in a git repository" in proc.stderr


# ── the verdict ──────────────────────────────────────────────────────────────

def test_a_step_without_a_marker_is_not_passed_and_fails_the_verdict(tmp_path: Path, ci_env) -> None:
    markers = _markers(tmp_path, ["prepare-git", "test-all-packages"])
    verdict = psv.build_verdict(workflow="full-suite", leg=None, steps=STEPS, markers=markers,
                                coverage=None, commit=SHA,
                                now=datetime(2026, 11, 17, 13, 40, tzinfo=timezone.utc))
    assert verdict == {
        "schema": 1, "workflow": "full-suite", "leg": None, "commit": SHA, "pipeline": 6480,
        "url": "https://ci.example/repos/1/pipeline/6480",
        "finished_at": "2026-11-17T13:40:00Z", "status": "failed",
        "steps": [{"name": "prepare-git", "result": "passed"},
                  {"name": "build-grammars", "result": "not_passed"},
                  {"name": "test-all-packages", "result": "passed"}],
        "coverage": None}


def test_the_verdict_passes_only_when_every_named_step_passed(tmp_path: Path, ci_env) -> None:
    markers = _markers(tmp_path, STEPS)
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    kw = {"workflow": "w", "leg": None, "markers": markers, "coverage": None, "commit": SHA,
          "now": now}
    assert psv.build_verdict(steps=STEPS, **kw)["status"] == "passed"
    assert psv.build_verdict(steps=[*STEPS, "never-marked"], **kw)["status"] == "failed"


def test_pipeline_and_url_are_null_outside_ci(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CI_PIPELINE_NUMBER", raising=False)
    monkeypatch.delenv("CI_PIPELINE_URL", raising=False)
    verdict = psv.build_verdict(workflow="w", leg="3.10", steps=["a"], markers=tmp_path,
                                coverage=None, commit=SHA, now=datetime.now(timezone.utc))
    assert verdict["pipeline"] is None and verdict["url"] is None and verdict["leg"] == "3.10"


def test_the_commit_comes_from_ci_or_head(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CI_COMMIT_SHA", SHA)
    assert psv._commit() == SHA
    monkeypatch.setenv("CI_COMMIT_SHA", "not-a-sha")
    with pytest.raises(psv.PublishError):
        psv._commit()
    monkeypatch.delenv("CI_COMMIT_SHA")
    repo = tmp_path / "ws"
    _git("init", "-q", str(repo))
    _git("commit", "-q", "--allow-empty", "-m", "x", cwd=repo)
    monkeypatch.chdir(repo)
    assert psv._commit() == _git("rev-parse", "HEAD", cwd=repo)


def test_coverage_lists_at_most_200_uncovered_lines_sorted(tmp_path: Path) -> None:
    missing = {"b.py": list(range(150, 0, -1)), "a.py": list(range(1, 101))}
    report = _coverage_report(tmp_path / "c.json", percent=97.5, statements=10000, missing=missing)
    cov = psv.load_coverage(report)
    assert (cov["percent"], cov["statements"], cov["missing"]) == (97.5, 10000, 250)
    assert list(cov["uncovered"]) == ["a.py", "b.py"]
    assert cov["uncovered"]["a.py"] == list(range(1, 101))
    assert cov["uncovered"]["b.py"] == list(range(1, 101))
    assert sum(map(len, cov["uncovered"].values())) == 200
    assert cov["uncovered_truncated"] is True
    small = psv.load_coverage(_coverage_report(tmp_path / "s.json", percent=99.0, statements=100,
                                               missing={"x.py": [3]}))
    assert small["uncovered"] == {"x.py": [3]} and small["uncovered_truncated"] is False


def test_an_absent_report_is_null_and_a_broken_one_is_an_error(tmp_path: Path) -> None:
    assert psv.load_coverage(tmp_path / "nope.json") is None
    (tmp_path / "bad.json").write_text("{\"totals\": {}}")
    with pytest.raises(ValueError):
        psv.load_coverage(tmp_path / "bad.json")


@pytest.mark.parametrize(("percent", "missing", "expected"), [
    (100.0, 0, ("100%", "brightgreen")),
    (99.97, 1, ("99%", "red")),
    (100.0, 1, ("99%", "red")),        # a rounded-up report still shows a missing line
    (99.2, 0, ("99%", "red")),         # missing branches only
    (87.9, 40, ("87%", "red")),
])
def test_the_coverage_badge_rounds_down_and_is_green_only_at_100(percent, missing, expected) -> None:
    cov = {"percent": percent, "statements": 3000, "missing": missing}
    assert psv.coverage_message(cov) == expected


def test_no_coverage_figure_reads_unknown() -> None:
    assert psv.coverage_message(None) == ("unknown", "lightgrey")


def _verdict(workflow: str, leg, status: str, coverage=None) -> dict:
    return {"workflow": workflow, "leg": leg, "status": status, "coverage": coverage}


def test_full_suite_writes_verdict_status_badge_and_coverage_badge() -> None:
    files = psv.files_for(_verdict("full-suite", None, "failed"))
    assert sorted(files) == ["scheduled/coverage-badge.json", "scheduled/full-suite-badge.json",
                             "scheduled/full-suite.json"]
    assert files["scheduled/full-suite-badge.json"] == {
        "schemaVersion": 1, "label": "full suite", "message": "failing", "color": "red"}
    assert files["scheduled/coverage-badge.json"] == {
        "schemaVersion": 1, "label": "coverage", "message": "unknown", "color": "lightgrey"}


def test_a_leg_writes_its_own_files_and_no_coverage_badge() -> None:
    files = psv.files_for(_verdict("nightly", "3.10", "passed",
                                   {"percent": 100.0, "statements": 1, "missing": 0}))
    assert sorted(files) == ["scheduled/nightly-3.10-badge.json", "scheduled/nightly-3.10.json"]
    assert files["scheduled/nightly-3.10-badge.json"] == {
        "schemaVersion": 1, "label": "nightly 3.10", "message": "passing", "color": "brightgreen"}


# ── publishing ───────────────────────────────────────────────────────────────

def test_publish_lands_the_files_and_keeps_the_branch_s_other_files(tmp_path: Path, ci_env, capsys) -> None:
    bare = _bare(tmp_path)
    _seed_badges(tmp_path, bare, {"coverage.json": '{"june": 1}\n', "last-green-sha.txt": "abc\n"})
    markers = _markers(tmp_path, ["prepare-git", "test-all-packages"])
    _coverage_report(markers / "coverage.json", percent=99.97, statements=3000,
                     missing={"packages/x/src/m.py": [12]})
    rc = psv.main(["publish", "--workflow", "full-suite", "--steps", *STEPS,
                   "--markers", str(markers), "--remote", str(bare)])
    assert rc == 0, capsys.readouterr().err
    assert sorted(_branch_files(bare)) == [
        "coverage.json", "last-green-sha.txt", "scheduled/coverage-badge.json",
        "scheduled/full-suite-badge.json", "scheduled/full-suite.json"]
    assert _branch_file(bare, "coverage.json") == '{"june": 1}'
    assert _branch_file(bare, "last-green-sha.txt") == "abc"
    verdict = json.loads(_branch_file(bare, "scheduled/full-suite.json"))
    assert verdict["status"] == "failed" and verdict["commit"] == SHA
    assert verdict["coverage"]["uncovered"] == {"packages/x/src/m.py": [12]}
    assert json.loads(_branch_file(bare, "scheduled/coverage-badge.json"))["message"] == "99%"
    log = _git("--git-dir", str(bare), "log", "-1", "--format=%an <%ae>|%s", "badges")
    assert log == f"CI Bot <ci@iterabloom.com>|scheduled verdict: full-suite failed at {SHA[:8]}"
    assert _git("--git-dir", str(bare), "rev-list", "--count", "badges") == "2"


def test_a_branch_the_remote_lacks_is_created_as_an_orphan(tmp_path: Path, ci_env) -> None:
    bare = _bare(tmp_path)
    markers = _markers(tmp_path, ["a"])
    assert psv.main(["publish", "--workflow", "nightly", "--leg", "3.12", "--steps", "a",
                     "--markers", str(markers), "--remote", str(bare)]) == 0
    assert sorted(_branch_files(bare)) == ["scheduled/nightly-3.12-badge.json",
                                           "scheduled/nightly-3.12.json"]
    assert _git("--git-dir", str(bare), "rev-list", "--count", "badges") == "1"
    subject = _git("--git-dir", str(bare), "log", "-1", "--format=%s", "badges")
    assert subject == f"scheduled verdict: nightly 3.12 passed at {SHA[:8]}"


def test_four_legs_publishing_at_once_all_land(tmp_path: Path) -> None:
    """The nightly's four legs push at about the same moment: none may be lost."""
    bare = _bare(tmp_path)
    _seed_badges(tmp_path, bare, {"coverage.json": "{}\n"})
    env = {**os.environ, "CI_COMMIT_SHA": SHA}
    procs = [subprocess.Popen(
        [str(SCRIPT), "publish", "--workflow", "nightly", "--leg", leg, "--steps", "a",
         "--markers", str(tmp_path / "none"), "--remote", str(bare)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        for leg in ("3.10", "3.11", "3.12", "3.13")]
    results = [p.communicate(timeout=120) + (p.returncode,) for p in procs]
    assert all(rc == 0 for _, _, rc in results), results
    files = _branch_files(bare)
    for leg in ("3.10", "3.11", "3.12", "3.13"):
        assert f"scheduled/nightly-{leg}.json" in files
    assert "coverage.json" in files
    assert _git("--git-dir", str(bare), "rev-list", "--count", "badges") == "5"


def test_a_leg_that_lands_between_fetch_and_push_is_kept(tmp_path: Path, ci_env, monkeypatch) -> None:
    """Deterministic form of the race: another leg pushes after this publish
    fetched the tip and before it pushed. The push must be rejected (no force)
    and redone on the new tip, so both legs' files are on the branch."""
    bare = _bare(tmp_path)
    _seed_badges(tmp_path, bare, {"coverage.json": "{}\n"})
    real_run = psv.Publisher._run
    pushes: list[str] = []

    def interleaved(self, *args: str, what: str, data=None):
        if args[0] == "push":
            pushes.append(args[-1])
            if len(pushes) == 1:
                other = tmp_path / "other"
                _git("clone", "-q", "--branch", "badges", str(bare), str(other))
                (other / "scheduled").mkdir()
                (other / "scheduled" / "nightly-3.13.json").write_text("{}\n")
                _git("add", ".", cwd=other)
                _git("commit", "-q", "-m", "other leg", cwd=other)
                _git("push", "-q", "origin", "HEAD:badges", cwd=other)
        return real_run(self, *args, what=what, data=data)

    monkeypatch.setattr(psv.Publisher, "_run", interleaved)
    assert psv.main(["publish", "--workflow", "nightly", "--leg", "3.10", "--steps", "a",
                     "--markers", str(tmp_path), "--remote", str(bare)]) == 0
    assert len(pushes) == 2
    files = _branch_files(bare)
    assert "scheduled/nightly-3.13.json" in files and "scheduled/nightly-3.10.json" in files
    assert "coverage.json" in files


def _rejecting_remote(tmp_path: Path) -> tuple[Path, Path]:
    bare = _bare(tmp_path)
    log = tmp_path / "attempts.log"
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(f"#!/bin/sh\necho attempt >> '{log}'\necho 'denied by policy' >&2\nexit 1\n")
    hook.chmod(0o755)
    return bare, log


def test_a_rejected_push_is_retried_then_fails_loudly(tmp_path: Path, ci_env, capsys) -> None:
    bare, log = _rejecting_remote(tmp_path)
    rc = psv.main(["publish", "--workflow", "full-suite", "--steps", "a",
                   "--markers", str(tmp_path), "--remote", str(bare)])
    err = capsys.readouterr().err
    assert rc == 1
    assert len(log.read_text().splitlines()) == psv.PUSH_ATTEMPTS
    assert len(err.strip().splitlines()) == 1, err
    assert "still rejected after 5 attempts" in err


def test_a_rejected_push_exits_1_as_a_process(tmp_path: Path) -> None:
    """The CI step sees the exit code, not a swallowed echo."""
    bare, _ = _rejecting_remote(tmp_path)
    proc = subprocess.run(
        [str(SCRIPT), "publish", "--workflow", "full-suite", "--steps", "a",
         "--markers", str(tmp_path), "--remote", str(bare)],
        capture_output=True, text=True, check=False, env={**os.environ, "CI_COMMIT_SHA": SHA},
        timeout=120)
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert len(proc.stderr.strip().splitlines()) == 1, proc.stderr


def test_an_unreadable_remote_fails_at_once(tmp_path: Path, ci_env, capsys) -> None:
    rc = psv.main(["publish", "--workflow", "w", "--steps", "a", "--markers", str(tmp_path),
                   "--remote", str(tmp_path / "no-such.git")])
    err = capsys.readouterr().err
    assert rc == 1 and err.startswith("publish-scheduled-verdict: reading ")
    assert len(err.strip().splitlines()) == 1


def test_no_remote_and_no_key_exit_1(tmp_path: Path, ci_env, capsys, monkeypatch) -> None:
    args = ["publish", "--workflow", "w", "--steps", "a", "--markers", str(tmp_path)]
    assert psv.main(args) == 1
    assert "no remote" in capsys.readouterr().err
    monkeypatch.setenv("CI_REPO", "iterabloom/hypergumbo")
    assert psv.main(args) == 1
    assert "no credential: BADGES_DEPLOY_KEY" in capsys.readouterr().err


def test_the_remote_defaults_to_the_ssh_clone_url_then_ci_repo(monkeypatch) -> None:
    monkeypatch.delenv("CI_REPO_CLONE_SSH_URL", raising=False)
    monkeypatch.setenv("CI_REPO", "iterabloom/hypergumbo")
    assert psv._default_remote() == "git@github.com:iterabloom/hypergumbo.git"
    monkeypatch.setenv("CI_REPO_CLONE_SSH_URL", "git@example.invalid:o/r.git")
    assert psv._default_remote() == "git@example.invalid:o/r.git"


@pytest.mark.parametrize(("url", "sent_to"), [
    ("git@github.com:iterabloom/hypergumbo.git",
     "ssh://git@ssh.github.com:443/iterabloom/hypergumbo.git"),
    ("ssh://git@github.com/iterabloom/hypergumbo.git",
     "ssh://git@ssh.github.com:443/iterabloom/hypergumbo.git"),
    ("ssh://git@github.com:22/o/r.git", "ssh://git@ssh.github.com:443/o/r.git"),
    # Already there, another host, another transport, a local path: as given.
    ("ssh://git@ssh.github.com:443/o/r.git", "ssh://git@ssh.github.com:443/o/r.git"),
    ("git@example.invalid:o/r.git", "git@example.invalid:o/r.git"),
    ("git@github.com.example.invalid:o/r.git", "git@github.com.example.invalid:o/r.git"),
    ("https://github.com/o/r.git", "https://github.com/o/r.git"),
    ("/srv/git/r.git", "/srv/git/r.git"),
])
def test_a_github_ssh_remote_goes_to_port_443_and_nothing_else_is_touched(
        url: str, sent_to: str) -> None:
    """Pipeline 6512 waited 120 s on github.com:22 and published nothing; the
    runner reaches GitHub on 443 only."""
    assert psv._over_443(url) == sent_to


KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAfakekeymaterialZZZZ\n-----END OPENSSH PRIVATE KEY-----"

#: Stand-in for ``ssh``: records its argv and what the files it is pointed at
#: hold at the moment git calls it, then refuses like an unreachable host.
FAKE_SSH = """#!/bin/sh
out="$FAKE_SSH_LOG"
printf '%s\\n' "$@" > "$out.argv"
key="" ; hosts=""
while [ $# -gt 0 ]; do
  case "$1" in
    -i) key="$2"; shift ;;
    -o) case "$2" in UserKnownHostsFile=*) hosts="${2#UserKnownHostsFile=}" ;; esac; shift ;;
  esac
  shift
done
echo "$key" > "$out.keypath"; stat -c %a "$key" > "$out.keymode"; cat "$key" > "$out.key"
echo "$hosts" > "$out.hostspath"; cat "$hosts" > "$out.hosts"
echo "fake ssh: connection refused" >&2
exit 255
"""


def test_the_deploy_key_path(tmp_path: Path, ci_env, capsys, monkeypatch) -> None:
    """Key in a 0600 file with a trailing newline, pinned host keys, strict
    checking, both files removed afterwards, the key in no argv and no output."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "ssh").write_text(FAKE_SSH)
    (bindir / "ssh").chmod(0o755)
    log = tmp_path / "ssh"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SSH_LOG", str(log))
    monkeypatch.setenv("CI_REPO", "iterabloom/hypergumbo")
    monkeypatch.setenv("BADGES_DEPLOY_KEY", KEY)
    rc = psv.main(["publish", "--workflow", "full-suite", "--steps", "a",
                   "--markers", str(tmp_path)])
    out, err = capsys.readouterr()
    assert rc == 1 and "fake ssh: connection refused" in err
    argv = Path(f"{log}.argv").read_text().splitlines()
    key_path = Path(f"{log}.keypath").read_text().strip()
    hosts_path = Path(f"{log}.hostspath").read_text().strip()
    assert argv[:7] == ["-i", key_path, "-o", "IdentitiesOnly=yes",
                        "-o", f"UserKnownHostsFile={hosts_path}", "-o"]
    assert "StrictHostKeyChecking=yes" in argv
    # GitHub's SSH service on the HTTPS port: the runner cannot reach port 22.
    assert argv[argv.index("-p"):argv.index("-p") + 3] == ["-p", "443", "git@ssh.github.com"]
    assert "git@github.com" not in argv
    # No prompt can be answered in CI, and a port that drops packets must be
    # reported by ssh in seconds, not by this script's own limit minutes later.
    assert "BatchMode=yes" in argv and "ConnectTimeout=20" in argv
    assert Path(f"{log}.keymode").read_text().strip() == "600"
    assert Path(f"{log}.key").read_text() == KEY + "\n"
    hosts = Path(f"{log}.hosts").read_text().splitlines()
    assert hosts == [f"{host} {k}" for host in ("github.com", "[ssh.github.com]:443")
                     for k in psv.GITHUB_HOST_KEYS] and len(hosts) == 6
    assert not Path(key_path).exists() and not Path(hosts_path).exists()
    assert "fakekeymaterial" not in "\n".join(argv) + out + err


#: Stand-in for an ``ssh`` whose connection is never answered.
HANGING_SSH = """#!/bin/sh
exec sleep 30
"""


def test_a_remote_that_never_answers_says_which_step_waited(
        tmp_path: Path, ci_env, capsys, monkeypatch) -> None:
    """The first scheduled run failed with 'git timed out after 120 s' and
    nothing else (pipeline 6512: the runner cannot reach github.com:22). The
    line must say what was being done and to which remote."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "ssh").write_text(HANGING_SSH)
    (bindir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CI_REPO", "iterabloom/hypergumbo")
    monkeypatch.setenv("BADGES_DEPLOY_KEY", KEY)
    monkeypatch.setattr(psv, "GIT_TIMEOUT", 1)
    rc = psv.main(["publish", "--workflow", "full-suite", "--steps", "a",
                   "--markers", str(tmp_path)])
    _out, err = capsys.readouterr()
    assert rc == 1
    assert err.strip() == (
        "publish-scheduled-verdict: reading ssh://git@ssh.github.com:443/iterabloom/hypergumbo.git "
        "timed out after 1 s: the remote did not answer (is its port reachable from this runner?)")


def test_the_key_file_is_private(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BADGES_DEPLOY_KEY", "k\n")
    env = psv._ssh_env(tmp_path)
    mode = stat.S_IMODE((tmp_path / "deploy_key").stat().st_mode)
    assert mode == 0o600 and (tmp_path / "deploy_key").read_text() == "k\n"
    assert "k\n" not in env["GIT_SSH_COMMAND"]


@pytest.mark.parametrize("pasted", ["l1\r\nl2\r\n", "l1\r\nl2", "l1\nl2\n", "l1\rl2\r"])
def test_a_key_pasted_with_other_line_endings_is_written_with_newlines(
        tmp_path: Path, monkeypatch, pasted: str) -> None:
    """A secret pasted through a web form can arrive with CRLF line ends, and
    ssh refuses such a key file as "invalid format"."""
    monkeypatch.setenv("BADGES_DEPLOY_KEY", pasted)
    psv._ssh_env(tmp_path)
    assert (tmp_path / "deploy_key").read_bytes() == b"l1\nl2\n"


def test_dry_run_contacts_nothing_and_needs_no_key(tmp_path: Path, ci_env, capsys) -> None:
    markers = _markers(tmp_path, ["a"])
    rc = psv.main(["publish", "--workflow", "full-suite", "--steps", "a", "--markers", str(markers),
                   "--dry-run"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["verdict"]["status"] == "passed"
    assert sorted(payload["files"]) == ["scheduled/coverage-badge.json",
                                        "scheduled/full-suite-badge.json", "scheduled/full-suite.json"]


def test_an_unreadable_coverage_report_warns_and_publishes_null(tmp_path: Path, ci_env, capsys) -> None:
    markers = _markers(tmp_path, ["a"])
    (markers / "coverage.json").write_text("{truncated")
    rc = psv.main(["publish", "--workflow", "full-suite", "--steps", "a", "--markers", str(markers),
                   "--dry-run"])
    out, err = capsys.readouterr()
    assert rc == 0 and "WARNING: coverage report unreadable" in err
    assert json.loads(out)["verdict"]["coverage"] is None


@pytest.mark.parametrize("argv", [
    ["publish", "--workflow", "w", "--steps", "a", "a"],
    ["publish", "--workflow", "../w", "--steps", "a"],
    ["publish", "--workflow", "w", "--leg", "3/10", "--steps", "a"],
    ["publish", "--workflow", "w", "--steps", "a/b"],
    ["publish", "--workflow", "w", "--steps", "a", "--branch", "-x"],
    ["publish", "--workflow", "w"],
    ["mark", ".hidden"],
    ["frobnicate"],
])
def test_bad_arguments_exit_2(argv: list[str], capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        psv.main(argv)
    assert exc.value.code == 2


#: Stand-in for ``ssh`` in the probe: records its argv, says what it is told to.
PROBE_SSH = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_SSH_LOG.argv"
for a in "$@"; do case "$a" in */deploy_key) cat "$a" > "$FAKE_SSH_LOG.key" ;; esac; done
[ -n "$FAKE_SSH_SAYS" ] && echo "$FAKE_SSH_SAYS" >&2
exit "$FAKE_SSH_RC"
"""

DENIED = "git@ssh.github.com: Permission denied (publickey)."
WELCOME = ("Hi iterabloom/hypergumbo! You've successfully authenticated, but GitHub does not "
           "provide shell access.")
REMOTE_443 = "ssh://git@ssh.github.com:443/iterabloom/hypergumbo.git"


@pytest.fixture
def fake_ssh(tmp_path: Path, monkeypatch):
    """Put a stand-in ``ssh`` first on PATH; returns (set what it says, read its argv)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "ssh").write_text(PROBE_SSH)
    (bindir / "ssh").chmod(0o755)
    log = tmp_path / "ssh"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SSH_LOG", str(log))
    monkeypatch.setenv("CI_REPO", "iterabloom/hypergumbo")
    monkeypatch.delenv("CI_REPO_CLONE_SSH_URL", raising=False)
    monkeypatch.delenv("BADGES_DEPLOY_KEY", raising=False)

    def says(text: str, rc: int) -> None:
        monkeypatch.setenv("FAKE_SSH_SAYS", text)
        monkeypatch.setenv("FAKE_SSH_RC", str(rc))

    return says, lambda: Path(f"{log}.argv").read_text().splitlines()


def test_the_probe_without_a_key_passes_when_github_answers_on_443(fake_ssh, capsys) -> None:
    """ssh checks the host key before it authenticates: 'Permission denied
    (publickey)' to a connection that offered no key means the runner reached
    port 443 and what answered holds one of the pinned keys."""
    says, argv = fake_ssh
    says(DENIED, 255)
    assert psv.main(["probe"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == (f"publish-scheduled-verdict: reached {REMOTE_443}: host key verified "
                           "(no key was offered; the push itself needs BADGES_DEPLOY_KEY)")
    assert err == ""
    args = argv()
    assert args[:2] == ["-o", "PubkeyAuthentication=no"] and "-i" not in args
    assert "StrictHostKeyChecking=yes" in args and "BatchMode=yes" in args
    assert args[-4:] == ["-p", "443", "-T", "git@ssh.github.com"]


def test_the_probe_with_the_key_says_whether_github_took_it(
        fake_ssh, capsys, monkeypatch, tmp_path: Path) -> None:
    says, argv = fake_ssh
    monkeypatch.setenv("BADGES_DEPLOY_KEY", KEY)
    says(WELCOME, 1)
    assert psv.main(["probe"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == (f"publish-scheduled-verdict: reached {REMOTE_443}: host key verified, "
                           "deploy key accepted")
    args = argv()
    key_path = args[args.index("-i") + 1]
    assert (tmp_path / "ssh.key").read_text() == KEY + "\n"
    assert not Path(key_path).exists(), "the key file is removed afterwards"
    assert "fakekeymaterial" not in "\n".join(args) + out + err
    # The same answer that passes without a key is a refusal with one.
    says(DENIED, 255)
    assert psv.main(["probe"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "the deploy key was refused" in err and REMOTE_443 in err


@pytest.mark.parametrize(("said", "rc", "reported"), [
    ("Host key verification failed.", 255, "Host key verification failed."),
    ("ssh: connect to host ssh.github.com port 443: Connection timed out", 255,
     "ssh: connect to host ssh.github.com port 443: Connection timed out"),
    ("kex_exchange_identification: Connection closed by remote host", 255,
     "kex_exchange_identification: Connection closed by remote host"),
    ("", 255, "ssh exit 255"),
])
def test_the_probe_fails_with_what_ssh_said(fake_ssh, capsys, said: str, rc: int,
                                            reported: str) -> None:
    says, _argv = fake_ssh
    says(said, rc)
    assert psv.main(["probe"]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == f"publish-scheduled-verdict: could not reach {REMOTE_443}: {reported}"


def test_the_probe_of_another_ssh_remote_uses_that_remotes_port(fake_ssh, capsys) -> None:
    says, argv = fake_ssh
    says(DENIED, 255)
    assert psv.main(["probe", "--remote", "git@example.invalid:o/r.git"]) == 0
    assert argv()[-2:] == ["-T", "git@example.invalid"] and "-p" not in argv()
    assert "reached git@example.invalid:o/r.git" in capsys.readouterr().out
    assert psv.main(["probe", "--remote", "ssh://git@example.invalid:2222/o/r.git"]) == 0
    assert argv()[-4:] == ["-p", "2222", "-T", "git@example.invalid"]


@pytest.mark.parametrize("remote", ["https://github.com/o/r.git", "/srv/git/r.git"])
def test_the_probe_of_a_remote_that_is_not_ssh_says_so(fake_ssh, capsys, remote: str) -> None:
    assert psv.main(["probe", "--remote", remote]) == 1
    assert capsys.readouterr().err.strip() == (
        f"publish-scheduled-verdict: not an ssh remote, nothing to probe: {remote}")


def test_the_probe_with_no_remote_to_derive_says_so(fake_ssh, capsys, monkeypatch) -> None:
    monkeypatch.delenv("CI_REPO")
    assert psv.main(["probe"]) == 1
    assert "no remote" in capsys.readouterr().err


def test_a_probe_that_is_never_answered_says_what_it_waited_on(
        tmp_path: Path, capsys, monkeypatch) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "ssh").write_text(HANGING_SSH)
    (bindir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CI_REPO", "iterabloom/hypergumbo")
    monkeypatch.delenv("CI_REPO_CLONE_SSH_URL", raising=False)
    monkeypatch.setattr(psv, "GIT_TIMEOUT", 1)
    assert psv.main(["probe"]) == 1
    assert capsys.readouterr().err.strip() == (
        f"publish-scheduled-verdict: reaching {REMOTE_443} timed out after 1 s: the remote "
        "did not answer (is its port reachable from this runner?)")
