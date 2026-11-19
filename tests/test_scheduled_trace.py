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


def test_a_pr_from_before_the_test_existed_is_not_named_for_code(repo: Repo) -> None:
    """The 2026-11-18 13:00 red (pipeline 6582): tests/test_kept_work_contexts.py
    failed in the scheduled environment; PR #2252 changed code it runs, before
    PR #2253 added the test (and its manifest ran it). #2252's manifest could
    not list a file that did not exist yet: it is not 'not run by its PR'."""
    before = repo.commit("code first", {"src/a.py": "def f():\n    return 2\n"})
    repo.manifest("manifest of the earlier PR", [])
    added = repo.commit("add the test", {"tests/test_new.py": "def test_n():\n    pass\n"})
    red = repo.manifest("manifest of the later PR", ["tests/test_new.py"])
    history = st.History(repo.root, repo.green, red)
    found = st.trace_test(history, "step", "tests/test_new.py::test_n", "boom",
                          ["src/a.py"], None, {})
    assert found.arm == "scheduled environment"
    assert [c.sha for c, _ in found.commits] == [added]
    assert before not in [c.sha for c, _ in found.commits]


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
    with pytest.raises(SystemExit, match=r"passed .*nothing to trace"):
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


# ── the ledger (WI-taguj) ───────────────────────────────────────────────────
#
# One row per scheduled run: the PRs of its range (each manifest commit), what
# each manifest left out, and what the run found that a PR's manifest had not
# run. What would break if each assertion went: the count of runs that found a
# selection miss (the ledger's one number), the row a run without a verdict
# must still get, and the "unknown" a red run with unread logs must say.

def _ledger_history(repo: Repo) -> dict[str, str]:
    """green (run 1) -> a PR that skips test_a, which then fails (run 2) ->
    a PR that lists every test file (run 3, passed). pytest's patterns come
    from the root configuration: a ``BRANCHES_*.py`` file is a test file,
    ``helper.py`` is not."""
    changed = repo.commit("break a", {
        "tests/test_a.py": "def test_a():\n    assert 0\n",
        "tests/test_b.py": "def test_b():\n    pass\n", "tests/helper.py": "",
        "tests/BRANCHES_test_c.py": "",
        "pyproject.toml": '[tool.pytest.ini_options]\npython_files = ["test_*.py", '
                          '"BRANCHES_*.py"]\n'})
    repo.manifest("manifest one, first try", [])
    red = repo.manifest("manifest one", ["tests/test_b.py"], ["tests/test_b.py::test_c"])
    fixed = repo.commit("fix a", {"tests/test_a.py": "def test_a():\n    pass\n"})
    later = repo.manifest("manifest two", ["tests/test_a.py", "tests/test_b.py",
                                           "tests/BRANCHES_test_c.py"])
    return {"changed": changed, "red": red, "fixed": fixed, "later": later}


def _verdicts(repo: Repo, h: dict[str, str]) -> list[dict]:
    return [
        {"commit": repo.green, "status": "passed", "pipeline": 1,
         "finished_at": "2026-11-18T01:00:00Z", "steps": []},
        {"commit": h["red"], "status": "failed", "pipeline": 2,
         "finished_at": "2026-11-18T13:00:00Z",
         "steps": [{"name": "test-agent-infra", "result": "not_passed"}],
         "coverage": {"uncovered": {}}},
        {"commit": h["later"], "status": "passed", "pipeline": 4,
         "finished_at": "2026-11-19T09:00:00Z", "steps": []},
    ]


def test_the_ledger_counts_the_runs_that_found_what_a_pr_left_out(
        repo: Repo, tmp_path: Path) -> None:
    h = _ledger_history(repo)
    _publish(repo, _verdicts(repo, h))
    stub = _stub_ci_debug(tmp_path, {"test-agent-infra": FAILED_A})
    rows = st.ledger(repo.root, "full-suite", ci_debug=str(stub))
    assert [(r.pipeline, r.status) for r in rows] == [(1, "passed"), (2, "failed"), (4, "passed")]
    first, red, green = rows
    assert first.previous is None and first.findings == []
    # The red run: its range holds one PR (the stub names every commit's PR
    # 77) with two manifest commits; the last, which listed one of the three
    # test files and deselected one test, is the one shown; test_a failed and
    # no manifest ran it.
    assert red.previous == repo.green and red.commits == 3
    [pr] = red.prs
    assert (pr.sha, pr.pr, pr.mode, pr.listed, pr.deselected, pr.left_out, pr.total,
            pr.manifests) == (h["red"], "PR #77", "targeted", 1, 1, 2, 3, 2)
    assert red.failed_steps == ["test-agent-infra"]
    assert [f.arm for f in red.findings] == ["not run by its PR"] and red.missed
    assert green.previous == h["red"] and [p.left_out for p in green.prs] == [0]
    assert green.findings == [] and not green.missed
    text = st.render_ledger(rows, "full-suite", pipelines=False)
    assert "full-suite ledger: 3 run(s), 3 with a published verdict" in text
    assert "runs without a published verdict are not listed: pass --pipelines" in text
    assert f"run 2 at {h['red'][:10]} (2026-11-18T13:00:00Z): failed (test-agent-infra)" in text
    assert f"  range {repo.green[:10]}..{h['red'][:10]}: 3 commit(s), 1 PR manifest(s)" in text
    assert (f"    PR #77 {h['red'][:10]} targeted: 1 test file(s) listed, 1 test(s) deselected, "
            "2 of 3 test file(s) left out (the last of its 2 manifest commits)") in text
    assert "  found: 1 failure(s) a PR's manifest did not run" in text
    assert "    tests/test_a.py: 1 failed (test_a)" in text
    assert "  range: no earlier run in the record" in text
    assert "  found: nothing: the run passed" in text
    assert ("selection misses: 1 of 2 run(s) with a verdict and a known range found a failure "
            "a PR's manifest did not run or an uncovered line traced into the range; "
            "0 unknown") in text


def test_a_run_with_no_verdict_is_a_row_and_unread_logs_are_unknown(
        repo: Repo, tmp_path: Path) -> None:
    h = _ledger_history(repo)
    _publish(repo, _verdicts(repo, h))
    pipelines = tmp_path / "pipelines.jsonl"
    pipelines.write_text("\n".join(json.dumps(r) for r in [
        {"number": 4, "event": "cron", "cron": "full-suite", "commit": h["later"], "created": 40},
        {"number": 3, "event": "cron", "cron": "full-suite", "commit": h["fixed"], "created": 30,
         "status": "killed"},
        {"number": 5, "event": "cron", "cron": "nightly", "commit": h["later"], "created": 50},
        {"number": 6, "event": "push", "commit": h["later"], "created": 60},
        {"number": 2, "event": "cron", "cron": "full-suite", "commit": h["red"], "created": 20},
    ]) + "\n")
    rows = st.ledger(repo.root, "full-suite", pipelines=pipelines)
    assert [(r.pipeline, r.status) for r in rows] == [
        (1, "passed"), (2, "failed"), (3, "no verdict"), (4, "passed")]
    _, red, lost, last = rows
    assert red.findings is None and "no --ci-debug" in red.unknown
    assert lost.findings is None and lost.unknown == "no verdict was published (pipeline killed)"
    assert lost.previous == h["red"] and last.previous == h["fixed"]
    # Without --ci-debug no PR is named: each manifest commit is its own row.
    assert [p.manifests for p in red.prs] == [1, 1]
    text = st.render_ledger(rows, "full-suite", pipelines=True)
    assert "3 commit(s), 2 manifest commit(s); the PRs are not named (no --ci-debug)" in text
    assert "full-suite ledger: 4 run(s), 3 with a published verdict" in text
    assert "not listed" not in text
    assert f"run 3 at {h['fixed'][:10]}: no verdict published" in text
    assert "  found: unknown: no verdict was published (pipeline killed)" in text
    assert "  found: unknown: the failed steps' logs were not read (no --ci-debug)" in text
    assert "0 of 1 run(s) with a verdict and a known range" in text and "; 2 unknown" in text


def test_the_ledger_names_an_uncovered_line_and_an_unreadable_range(
        repo: Repo, tmp_path: Path) -> None:
    changed = repo.commit("branch", {"src/a.py": "def f(x):\n    if x:\n        return 2\n    return 1\n"})
    manifest = repo.manifest("manifest", ["tests/test_a.py"])
    _publish(repo, [
        {"commit": "f" * 40, "status": "failed", "pipeline": 1, "steps": []},
        {"commit": repo.green, "status": "failed", "pipeline": 2, "steps": []},
        {"commit": manifest, "status": "failed", "pipeline": 3, "steps": [],
         "coverage": {"uncovered": {"src/a.py": [3]}, "uncovered_truncated": True}},
    ])
    rows = st.ledger(repo.root, "full-suite", ci_debug=str(_stub_ci_debug(tmp_path, {})))
    # A red run with no range is not known, never clean: the first run has no
    # earlier one, the second a range this clone cannot read.
    assert rows[0].findings is None
    assert rows[0].unknown == "no earlier run in the record: the range is not known"
    assert rows[1].commits is None and rows[1].prs == [] and rows[1].findings is None
    assert rows[1].unknown.startswith("the range is not readable")
    [gap] = rows[2].findings
    assert (gap.arm, gap.what, [c.sha for c, _ in gap.commits]) == ("coverage", "src/a.py:3", [changed])
    assert rows[2].missed
    text = st.render_ledger(rows, "full-suite", pipelines=False)
    assert "  found: 1 uncovered line(s) traced to a commit of the range" in text
    assert "    src/a.py:3 -> " in text and "the verdict lists only part of the uncovered lines" in text
    assert "  range: the range is not readable" in text


def test_the_ledger_cli_text_json_and_refusals(
        repo: Repo, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="no published verdict for full-suite"):
        st.ledger(repo.root, "full-suite")
    h = _ledger_history(repo)
    _publish(repo, _verdicts(repo, h))
    argv = ["--repo-root", str(repo.root), "--ledger", "full-suite"]
    assert st.main(argv) == 0
    assert "full-suite ledger: 3 run(s)" in capsys.readouterr().out
    assert st.main([*argv, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [(r["pipeline"], r["status"], r["missed"]) for r in data] == [
        (1, "passed", False), (2, "failed", None), (4, "passed", False)]
    assert data[1]["prs"][-1]["left_out"] == 2 and data[1]["findings"] is None


def test_the_ledger_separates_what_is_not_a_selection_miss(repo: Repo, tmp_path: Path) -> None:
    """A test its PR ran (the scheduled environment), a red run whose logs
    show nothing, a manifest with no mode line, a verdict naming no pipeline."""
    changed = repo.commit("add a case", {"tests/test_a.py": "def test_a():\n    assert 0\n"})
    ran = repo.manifest("manifest", ["tests/test_a.py"])
    bare = repo.commit("bare manifest", {st.MANIFEST: "# === SELECTED_TESTS ===\n"})
    _publish(repo, [
        {"commit": repo.green, "status": "passed", "steps": []},
        {"commit": ran, "status": "failed", "pipeline": 2,
         "steps": [{"name": "test-agent-infra", "result": "not_passed"}]},
        {"commit": bare, "status": "failed", "pipeline": 3, "steps": []},
    ])
    pipelines = tmp_path / "p.jsonl"
    pipelines.write_text("\n" + json.dumps({"number": 9, "event": "cron", "cron": "full-suite",
                                            "commit": bare}) + "\n")
    stub = _stub_ci_debug(tmp_path, {"test-agent-infra": FAILED_A})
    rows = st.ledger(repo.root, "full-suite", ci_debug=str(stub), pipelines=pipelines)
    assert [r.pipeline for r in rows] == [None, 2, 3, 9]
    assert rows[3].unknown == "no verdict was published"
    env, empty = rows[1], rows[2]
    assert [f.arm for f in env.findings] == ["scheduled environment"] and env.missed is False
    assert [c.sha for c, _ in env.findings[0].commits] == [changed]
    assert empty.findings == [] and empty.prs[0].mode == ""
    text = st.render_ledger(rows, "full-suite", pipelines=True)
    assert f"run ? at {repo.green[:10]}: passed" in text
    assert "  also found, not a selection miss: 1\n    [scheduled environment] tests/test_a.py" in text
    assert "  found: no failed test, flagged callable or uncovered line in what was read" in text
    assert f"    PR #77 {bare[:10]} mode not stated: 0 test file(s) listed" in text
    assert "selection misses: 0 of 2 run(s)" in text and "; 1 unknown" in text
