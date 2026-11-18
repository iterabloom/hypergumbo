# SPDX-License-Identifier: AGPL-3.0-or-later
"""``scripts/scheduled-trace`` (``ci-debug trace``): which commits a red
scheduled run points at, per kind of failure (WI-finat).

Each arm is driven on a throwaway git repository whose history is built for
it: a green commit, the commits of the range, and the manifests their PRs
committed. The logs are fabricated in the shapes the real ones have (pytest's
summary lines, the test-only reachability gate's ``  + path::name`` lines,
pip's ``Successfully installed``). The known answer on the real history (the
red runs of 2026-11-16 and 2026-11-18) is in the tracker item and the lab
notebook, not here: it needs the real logs.
"""

# covers: scripts/scheduled-trace
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "scheduled-trace"


def _load() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("scheduled_trace", str(SCRIPT))
    spec = importlib.util.spec_from_loader("scheduled_trace", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look their module up by name while the class is built.
    sys.modules["scheduled_trace"] = module
    loader.exec_module(module)
    return module


st = _load()


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.git("init", "-q", "-b", "dev")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.green = self.commit("base", {"src/a.py": "def f():\n    return 1\n",
                                          "tests/test_a.py": "def test_a():\n    pass\n"})

    def git(self, *argv: str) -> str:
        return subprocess.run(["git", "-C", str(self.root), *argv], capture_output=True,
                              text=True, check=True).stdout

    def commit(self, subject: str, files: dict[str, str]) -> str:
        for rel, text in files.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)
        return self.git("rev-parse", "HEAD").strip()

    def manifest(self, subject: str, tests: list[str], deselected: list[str] = ()) -> str:
        body = "# Mode: targeted\n" + "".join(f"#- {t}\n" for t in deselected)
        body += "# === CHANGED_SOURCE_FILES ===\n# === SELECTED_TESTS ===\n"
        return self.commit(subject, {st.MANIFEST: body + "".join(f"{t}\n" for t in tests)})


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


FAILED_A = "FAILED tests/test_a.py::test_a - AssertionError: boom\n"


def _trace(repo: Repo, red: str, logs: dict[str, str], **kw) -> list:
    return st.trace(repo.root, repo.green, red, logs, kw.get("green_logs", {}),
                    kw.get("uncovered", {}), None)


# ── parsing ─────────────────────────────────────────────────────────────────

def test_failed_tests_read_ids_with_spaces_in_their_parameters() -> None:
    log = ("\x1b[31mFAILED tests/test_x.py::test_p[tuple[*tuple[int, ...]] - x] -   File x\x1b[0m\n"
           "ERROR tests/test_y.py - ModuleNotFoundError: no\n"
           "FAILED tests/test_x.py::test_q - a - b\n"
           "FAILED TESTS:\n")
    assert st.failed_tests(log) == {
        "tests/test_x.py::test_p[tuple[*tuple[int, ...]] - x]": "File x",
        "tests/test_y.py": "ModuleNotFoundError: no",
        "tests/test_x.py::test_q": "a - b"}


def test_gate_flags_and_installed_versions() -> None:
    log = ("FAIL: 2 callable(s) are newly reachable only from their own tests:\n"
           "  + pkg/src/m.py::helper\n  + pkg/src/n.py::C.method\n"
           "  - pkg/src/o.py::drained\n"
           "Successfully installed pip-26.3\n"
           "Successfully installed pytest-9.2.0 pytest-cov-7.1.0 tree-sitter-0.25.2\n")
    assert st.gate_flags(log) == [("pkg/src/m.py", "helper"), ("pkg/src/n.py", "C.method")]
    assert st.installed(log) == {"pip": "26.3", "pytest": "9.2.0", "pytest-cov": "7.1.0",
                                 "tree-sitter": "0.25.2"}
    assert st.version_changes({"pytest": "9.1.0", "a": "1"}, {"pytest": "9.2.0", "b": "2"}) == [
        "a 1 -> (absent)", "b (absent) -> 2", "pytest 9.1.0 -> 9.2.0"]


# ── the arms ────────────────────────────────────────────────────────────────

def test_a_test_its_pr_ran_points_at_the_scheduled_environment(repo: Repo) -> None:
    changed = repo.commit("add a case", {"tests/test_a.py": "def test_a():\n    assert 0\n"})
    red = repo.manifest("manifest", ["tests/test_a.py"])
    [found] = _trace(repo, red, {"test-agent-infra": FAILED_A})
    assert found.arm == "scheduled environment"
    assert [(c.sha, why) for c, why in found.commits] == [
        (changed, "changed the test; its PR's CI ran it and passed")]


@pytest.mark.parametrize("listed,deselected", [([], []), (["tests/test_a.py"], ["tests/test_a.py::test_a"])])
def test_a_test_its_pr_did_not_run(repo: Repo, listed: list[str], deselected: list[str]) -> None:
    """Not listed, or listed and deselected by statement: not run."""
    changed = repo.commit("add a case", {"tests/test_a.py": "def test_a():\n    assert 0\n"})
    red = repo.manifest("manifest", listed, deselected)
    [found] = _trace(repo, red, {"step": FAILED_A})
    assert found.arm == "not run by its PR"
    assert [c.sha for c, _ in found.commits] == [changed]


def test_code_the_test_runs_names_only_prs_that_did_not_run_it(repo: Repo) -> None:
    ran_it = repo.commit("one", {"src/a.py": "def f():\n    return 2\n"})
    repo.manifest("manifest one", ["tests/test_a.py"])
    skipped_it = repo.commit("two", {"src/a.py": "def f():\n    return 3\n"})
    red = repo.manifest("manifest two", ["tests/test_other.py"])
    history = st.History(repo.root, repo.green, red)
    found = st.trace_test(history, "step", "tests/test_a.py::test_a", "boom",
                          ["src/a.py"], None, {})
    assert found.arm == "not run by its PR"
    assert [c.sha for c, _ in found.commits] == [skipped_it]
    assert ran_it not in [c.sha for c, _ in found.commits]


def test_a_commit_with_no_manifest_after_it_is_not_named_for_code(repo: Repo) -> None:
    red = repo.commit("unmanifested", {"src/a.py": "def f():\n    return 9\n"})
    history = st.History(repo.root, repo.green, red)
    found = st.trace_test(history, "s", "tests/test_a.py::test_a", "m", ["src/a.py"], None, {})
    assert found.arm == "dependency" and found.commits == []


def test_no_commit_touched_it_compares_installed_versions(repo: Repo) -> None:
    red = repo.commit("unrelated", {"src/b.py": "x = 1\n"})
    green = "Successfully installed pytest-9.1.0 execnet-2.1.2\n"
    red_log = FAILED_A + "Successfully installed pytest-9.2.0 execnet-2.1.2\n"
    [found] = _trace(repo, red, {"step": red_log}, green_logs={"step": green})
    assert found.arm == "dependency" and found.commits == []
    assert found.note.endswith("installed versions that differ: pytest 9.1.0 -> 9.2.0")
    [same] = _trace(repo, red, {"step": red_log},
                    green_logs={"step": "Successfully installed pytest-9.2.0 execnet-2.1.2\n"})
    assert same.note.endswith("the installed versions are the same: no cause found")
    [unknown] = _trace(repo, red, {"step": red_log})
    assert "no green log was given" in unknown.note


def test_a_gate_names_the_commits_that_added_or_removed_the_name(repo: Repo) -> None:
    added = repo.commit("add helper", {"src/b.py": "def helper():\n    return 1\n"})
    repo.commit("unrelated", {"src/c.py": "x = 2\n"})
    caller_gone = repo.commit("drop the caller", {"src/a.py": "def f():\n    return 1\n# helper\n"})
    red = repo.commit("remove mention", {"src/a.py": "def f():\n    return 1\n"})
    [found] = _trace(repo, red, {"self-tree-validation": "  + src/b.py::helper\n"})
    assert found.arm == "gate"
    assert [c.sha for c, _ in found.commits] == [added, caller_gone, red]
    [none] = _trace(repo, red, {"self-tree-validation": "  + src/b.py::absent_name\n"})
    assert none.commits == [] and "no commit of the range added or removed absent_name" in none.note


def test_a_coverage_gap_on_a_changed_line_names_its_commit(repo: Repo) -> None:
    changed = repo.commit("branch", {"src/a.py": "def f(x):\n    if x:\n        return 2\n    return 1\n"})
    red = repo.commit("unrelated", {"src/c.py": "x = 2\n"})
    [found] = _trace(repo, red, {}, uncovered={"src/a.py": [3]})
    assert found.arm == "coverage" and [c.sha for c, _ in found.commits] == [changed]


def test_a_coverage_gap_on_an_unchanged_line_names_the_tests_commits(
        repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    removed = repo.commit("drop the test", {"tests/test_a.py": "def test_other():\n    pass\n"})
    red = repo.commit("unrelated", {"src/c.py": "x = 2\n"})
    monkeypatch.setattr(st, "tests_on_line", lambda *a: ["tests/test_a.py"])
    [found] = _trace(repo, red, {}, uncovered={"src/a.py": [2]})
    assert [(c.sha, why) for c, why in found.commits] == [
        (removed, "changed or removed a test recorded on this line")]
    monkeypatch.setattr(st, "tests_on_line", lambda *a: [])
    [bare] = _trace(repo, red, {}, uncovered={"src/a.py": [2]})
    assert bare.commits == [] and "records no test on it" in bare.note
    monkeypatch.setattr(st, "tests_on_line", lambda *a: ["tests/test_untouched.py"])
    [kept] = _trace(repo, red, {}, uncovered={"src/a.py": [2]})
    assert kept.commits == [] and "no commit changed a test recorded on it" in kept.note


def test_failed_tests_of_one_file_are_one_finding(repo: Repo) -> None:
    changed = repo.commit("cases", {"tests/test_a.py": "def test_a():\n    assert 0\n"})
    red = repo.manifest("manifest", ["tests/test_a.py"])
    log = "".join(f"FAILED tests/test_a.py::test_{i} - E{i}\n" for i in range(7))
    [found] = _trace(repo, red, {"step": log})
    assert found.what.startswith("tests/test_a.py: 7 failed (test_0, test_1, test_2, test_3, "
                                 "test_4 and 2 more); first: E0")
    assert [c.sha for c, _ in found.commits] == [changed]


def test_the_index_says_what_a_test_runs_and_who_ran_a_line(repo: Repo, tmp_path: Path) -> None:
    from hypergumbo_core import selection_index as si
    from hypergumbo_core.block_hash import blocks_for_source

    source = (repo.root / "src/a.py").read_text()
    key = str(repo.root / "src/a.py")
    test = f"{repo.root}/tests/test_a.py::test_a"
    index = tmp_path / "index.sqlite"
    conn = si.open_index(index)
    digests = {key: {b.name: b.digest for b in blocks_for_source(key, source)}}
    si.store_edges(conn, {test: [(key, "f")]}, digests=digests,
                   statements={test: {(key, "f"): si._int_bitmap(1)}})
    conn.commit()
    conn.close()
    assert st.touched_files(index, "tests/test_a.py::test_a", repo.root) == ["src/a.py"]
    assert st.touched_files(None, "tests/test_a.py::test_a", repo.root) == []
    assert st.tests_on_line(index, "src/a.py", 2, repo.root, source) == ["tests/test_a.py"]
    assert st.tests_on_line(tmp_path / "absent.sqlite", "src/a.py", 2, repo.root, source) == []


# ── the command ─────────────────────────────────────────────────────────────

def _stub_ci_debug(tmp_path: Path, logs: dict[str, str], pr: str = "77") -> Path:
    stub = tmp_path / "ci-debug"
    arms = "".join(f'  "logs {step} "*) cat {str(tmp_path / (step + ".log"))!r} ;;\n'
                   for step in logs)
    for step, text in logs.items():
        (tmp_path / f"{step}.log").write_text(text)
    stub.write_text(f'#!/usr/bin/env bash\ncase "$*" in\n{arms}'
                    f'  "pr-of "*) echo {pr} ;;\n  *) exit 1 ;;\nesac\n')
    stub.chmod(0o755)
    return stub


def _publish(repo: Repo, verdicts: list[dict]) -> None:
    """Commit each verdict in turn on a ``badges`` branch and point
    ``origin/badges`` at it."""
    head = repo.git("rev-parse", "HEAD").strip()
    repo.git("branch", "-D", "badges") if "badges" in repo.git("branch") else None
    repo.git("checkout", "-q", "--orphan", "badges")
    for verdict in verdicts:
        repo.commit(f"verdict {verdict['status']}",
                    {"scheduled/full-suite.json": json.dumps(verdict)})
    repo.git("update-ref", "refs/remotes/origin/badges", "HEAD")
    repo.git("checkout", "-q", "-f", head)


def test_from_badges_takes_the_range_steps_and_logs(
        repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    changed = repo.commit("add a case", {"tests/test_a.py": "def test_a():\n    assert 0\n"})
    red = repo.manifest("manifest", ["tests/test_a.py"])
    _publish(repo, [
        {"commit": repo.green, "status": "passed", "steps": []},
        {"commit": changed, "status": "failed", "steps": []},
        {"commit": red, "status": "failed", "pipeline": 9,
         "steps": [{"name": "prepare", "result": "passed"},
                   {"name": "test-agent-infra", "result": "not_passed"}],
         "coverage": {"uncovered": {}, "uncovered_truncated": True}}])
    (tmp_path / "stub").mkdir()
    stub = _stub_ci_debug(tmp_path / "stub", {"test-agent-infra": FAILED_A})
    assert st.main(["--repo-root", str(repo.root), "--from-badges", "full-suite",
                    "--ci-debug", str(stub)]) == 0
    out = capsys.readouterr().out
    assert f"full-suite: red at {red[:10]} (pipeline 9), last passed at {repo.green[:10]}" in out
    assert "failed steps: test-agent-infra" in out
    assert "only part of the uncovered lines" in out
    assert f"-> PR #77 {changed[:10]} add a case  (changed the test; its PR's CI ran it" in out


def test_from_badges_refuses_what_it_cannot_trace(repo: Repo, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no published verdict"):
        st.badges_range(repo.root, "full-suite")
    _publish(repo, [{"commit": repo.green, "status": "failed", "steps": []}])
    with pytest.raises(SystemExit, match="no passed full-suite verdict is published"):
        st.badges_range(repo.root, "full-suite")
    _publish(repo, [{"commit": repo.green, "status": "passed", "steps": []}])
    with pytest.raises(SystemExit, match="passed .*nothing to trace"):
        st.badges_range(repo.root, "full-suite")


def test_the_explicit_range_with_a_verdict_and_json(
        repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    changed = repo.commit("branch", {"src/a.py": "def f(x):\n    if x:\n        return 2\n    return 1\n"})
    verdict = tmp_path / "verdict.json"
    verdict.write_text(json.dumps({"coverage": {"uncovered": {"src/a.py": [3]}}}))
    log = tmp_path / "step.log"
    log.write_text("  + src/a.py::f\n")
    argv = ["--repo-root", str(repo.root), "--green", repo.green, "--red", changed,
            "--verdict", str(verdict), "--log", f"gate={log}"]
    assert st.main([*argv, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [(d["arm"], d["what"]) for d in data] == [("gate", "src/a.py::f"), ("coverage", "src/a.py:3")]
    assert st.main(argv) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"range {repo.green[:10]}..{changed[:10]}: 1 commit(s)")
    assert f"-> {changed[:10]} branch  (last changed this line)" in out


def test_nothing_found_is_said(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    red = repo.commit("x", {"src/c.py": "x = 2\n"})
    assert st.main(["--repo-root", str(repo.root), "--green", repo.green, "--red", red]) == 0
    assert "no failed test, flagged callable or uncovered line found" in capsys.readouterr().out


@pytest.mark.parametrize("argv,message", [
    ([], "give --green and --red, or --from-badges"),
    (["--from-badges", "full-suite"], "--from-badges needs --ci-debug"),
])
def test_usage_errors(argv: list[str], message: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        st.main(argv)
    assert message in capsys.readouterr().err


def test_ci_debug_wires_trace_and_pr_of() -> None:
    text = (REPO_ROOT / "scripts" / "ci-debug").read_text()
    assert '--from-badges "${1:-full-suite}" --ci-debug "$SCRIPT_DIR/ci-debug"' in text
    assert 'api_get "$API_BASE/commits/$sha/pulls"' in text
    assert "  trace)\n    shift\n    cmd_trace" in text and "  pr-of)\n    cmd_pr_of" in text
