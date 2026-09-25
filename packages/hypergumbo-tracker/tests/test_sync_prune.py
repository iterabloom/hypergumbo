# SPDX-License-Identifier: MPL-2.0
"""WI-torug: a superseded ``tracker-sync/*`` branch must be prunable.

THE SITUATION THIS EXISTS FOR, measured 2026-09-02. Five ``tracker-sync/*`` PRs
sat open, the oldest from August 24, all showing conflicts on the forge and none
mergeable. They were not carrying stranded work: every one produced a merge tree
IDENTICAL to ``origin/dev``'s, because the ops they held had already reached dev
by a later sync.

WHY THEY CONFLICT AT ALL, since it is not obvious. ``.gitattributes`` declares
``merge=union`` on ``.ops`` — the whole point of an append-only journal. That is
a CLIENT-SIDE driver. The forge's server-side merge does not read
``.gitattributes``, so two branches that appended to the end of one file from a
common base are a textual conflict there and a trivial union anywhere else.
Measured on ``.INV-juvul-*.ops``: merge-base 32 lines, branch tip 54, current dev
61 — both sides appended, different content, same location.

WHY NOTHING IS AT RISK, and it is why pruning is the right verb rather than
merging. ``do_sync``'s post-sync cleanup is gated on merge success (WI-lufal), so
a sync that fails to merge leaves its ops in the working tree, and the NEXT sync
rebuilds from a freshly fetched base and carries them forward. The data always
arrives; the abandoned branch is litter.

THE SAFETY PROPERTY THESE TESTS EXIST TO PIN. The predicate must never call a
branch superseded while it holds an op dev does not have.
:class:`TestBranchWithUniqueOpsIsNeverSuperseded` is the load-bearing case: if it
ever passes vacuously, the prune becomes a data-destroying operation rather than
a tidying one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_tracker.sync import (
    _git,
    find_superseded_sync_branches,
)


OPS_DIR = ".agent/tracker-workspace/.ops"


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "config", "user.email", "a@b.c")
    _git(root, "config", "user.name", "Test")
    # The union driver is declared in the real repo; declare it here too so the
    # fixture exercises the same merge semantics a client actually uses.
    (root / ".gitattributes").write_text(
        f"{OPS_DIR}/.*.ops  merge=union\n"
    )
    ops = root / OPS_DIR
    ops.mkdir(parents=True)
    (ops / ".WI-seed.ops").write_text("op: create\nnonce: seed\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")


def _append_op(root: Path, item: str, text: str) -> None:
    p = root / OPS_DIR / f".{item}.ops"
    with p.open("a") as fh:
        fh.write(text)


def _commit_all(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repo whose ``dev`` has advanced past a stale sync branch.

    Reproduces the measured shape: the branch appended to an op log, then dev
    appended DIFFERENT lines to the SAME log, so the two diverge at the append
    point exactly as the five real branches did.
    """
    _init_repo(tmp_path)

    _git(tmp_path, "checkout", "-q", "-b", "tracker-sync/20260824-160016")
    _append_op(tmp_path, "WI-seed", "op: update\nnonce: from-branch\n")
    _commit_all(tmp_path, "tracker: 1 file (2 ops)")

    _git(tmp_path, "checkout", "-q", "dev")
    # dev gets the branch's op AND more, which is what a later sync produces:
    # the working tree still held the unmerged op, so the next sync carried it.
    _append_op(tmp_path, "WI-seed", "op: update\nnonce: from-branch\n")
    _append_op(tmp_path, "WI-seed", "op: update\nnonce: later-sync\n")
    _commit_all(tmp_path, "tracker: later sync carrying it forward")
    return tmp_path


class TestSupersededBranchIsDetected:
    """The five-real-branches case: everything the branch holds is in dev."""

    def test_stale_sync_branch_is_reported(self, repo: Path) -> None:
        found = find_superseded_sync_branches(repo, base_branch="dev", remote="")
        assert found == ["tracker-sync/20260824-160016"]


class TestBranchWithUniqueOpsIsNeverSuperseded:
    """THE LOAD-BEARING SAFETY TEST. Pruning must never destroy an op.

    A branch holding a line dev does not have is NOT superseded, even though it
    conflicts on the forge for exactly the same reason a superseded one does.
    Conflict status and supersession are independent, and confusing them is how
    a tidying step becomes data loss.
    """

    def test_branch_with_an_op_dev_lacks_is_not_superseded(
        self, repo: Path
    ) -> None:
        _git(repo, "checkout", "-q", "-b", "tracker-sync/20260902-999999")
        _append_op(repo, "WI-seed", "op: update\nnonce: ONLY-HERE\n")
        _commit_all(repo, "tracker: an op dev has never seen")
        _git(repo, "checkout", "-q", "dev")

        found = find_superseded_sync_branches(repo, base_branch="dev", remote="")
        assert "tracker-sync/20260902-999999" not in found

    def test_the_unique_op_really_is_absent_from_dev(self, repo: Path) -> None:
        """Guards the test above from passing vacuously."""
        _git(repo, "checkout", "-q", "-b", "tracker-sync/20260902-999999")
        _append_op(repo, "WI-seed", "op: update\nnonce: ONLY-HERE\n")
        _commit_all(repo, "tracker: an op dev has never seen")
        _git(repo, "checkout", "-q", "dev")

        dev_text = _git(repo, "show", f"dev:{OPS_DIR}/.WI-seed.ops").stdout
        assert "ONLY-HERE" not in dev_text


class TestScopeIsLimitedToSyncBranches:
    """A feature branch is never a prune candidate, however redundant."""

    def test_non_sync_branch_is_ignored(self, repo: Path) -> None:
        _git(repo, "checkout", "-q", "-b", "jgstern-agent/feat/whatever")
        _commit_all_noop = _git(
            repo, "commit", "-q", "--allow-empty", "-m", "empty"
        )
        assert _commit_all_noop.returncode == 0
        _git(repo, "checkout", "-q", "dev")

        found = find_superseded_sync_branches(repo, base_branch="dev", remote="")
        assert not any(b.startswith("jgstern-agent/") for b in found)

    def test_the_base_branch_is_never_a_candidate(self, repo: Path) -> None:
        found = find_superseded_sync_branches(repo, base_branch="dev", remote="")
        assert "dev" not in found


class TestUndecidableCasesPruneNothing:
    """Fail SAFE: when the predicate cannot be evaluated, report nothing.

    ``git merge-tree --write-tree`` needs git >= 2.38. On an older client the
    answer is unknown, and an unknown must not be read as "superseded" — that
    would close PRs on the strength of a missing feature.
    """

    def test_missing_base_branch_yields_no_candidates(self, repo: Path) -> None:
        found = find_superseded_sync_branches(
            repo, base_branch="no-such-branch", remote=""
        )
        assert found == []

    def test_not_a_git_repo_yields_no_candidates(self, tmp_path: Path) -> None:
        found = find_superseded_sync_branches(
            tmp_path / "nope", base_branch="dev", remote=""
        )
        assert found == []


class TestPruneAction:
    """``prune_superseded_sync_branches`` — the side-effecting half.

    The forge calls are stubbed. What is worth testing here is not that a PATCH
    was issued but the DECISIONS around it: that dry-run reports without acting,
    that a fetch failure prunes nothing (stale refs would misjudge the base), and
    that the branch list acted on is exactly the predicate's output.
    """

    def _stub_forge(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
        import hypergumbo_tracker.sync as sync_mod

        seen: dict[str, list] = {"closed": [], "deleted": []}
        monkeypatch.setattr(sync_mod, "_load_env", lambda _r: {"HG_GITHUB_TOKEN": "t"})
        monkeypatch.setattr(sync_mod, "_detect_api_base", lambda _r: "https://api/x")
        monkeypatch.setattr(sync_mod, "_find_open_pr", lambda *a, **k: (42, "sha"))
        monkeypatch.setattr(
            sync_mod, "_close_pr",
            lambda _a, _t, n: seen["closed"].append(n) or True,
        )
        monkeypatch.setattr(
            sync_mod, "_delete_remote_branch",
            lambda _a, _t, b: seen["deleted"].append(b) or True,
        )
        return seen

    def test_dry_run_reports_without_closing_anything(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hypergumbo_tracker.sync as sync_mod

        seen = self._stub_forge(monkeypatch)
        monkeypatch.setattr(
            sync_mod, "find_superseded_sync_branches",
            lambda *a, **k: ["tracker-sync/stale"],
        )
        monkeypatch.setattr(sync_mod, "_git", lambda *a, **k: _ok())

        out = sync_mod.prune_superseded_sync_branches(repo, dry_run=True)
        assert out == ["tracker-sync/stale"]
        assert seen["closed"] == [] and seen["deleted"] == []

    def test_acting_closes_the_pr_and_deletes_the_branch(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hypergumbo_tracker.sync as sync_mod

        seen = self._stub_forge(monkeypatch)
        monkeypatch.setattr(
            sync_mod, "find_superseded_sync_branches",
            lambda *a, **k: ["tracker-sync/stale"],
        )
        monkeypatch.setattr(sync_mod, "_git", lambda *a, **k: _ok())

        out = sync_mod.prune_superseded_sync_branches(repo, dry_run=False)
        assert out == ["tracker-sync/stale"]
        assert seen["closed"] == [42]
        assert seen["deleted"] == ["tracker-sync/stale"]

    def test_a_branch_with_no_open_pr_is_still_branch_deleted(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A superseded branch may have had its PR closed by hand already."""
        import hypergumbo_tracker.sync as sync_mod

        seen = self._stub_forge(monkeypatch)
        monkeypatch.setattr(sync_mod, "_find_open_pr", lambda *a, **k: None)
        monkeypatch.setattr(
            sync_mod, "find_superseded_sync_branches",
            lambda *a, **k: ["tracker-sync/stale"],
        )
        monkeypatch.setattr(sync_mod, "_git", lambda *a, **k: _ok())

        sync_mod.prune_superseded_sync_branches(repo, dry_run=False)
        assert seen["closed"] == []
        assert seen["deleted"] == ["tracker-sync/stale"]

    def test_nothing_superseded_makes_no_forge_calls(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hypergumbo_tracker.sync as sync_mod

        seen = self._stub_forge(monkeypatch)
        monkeypatch.setattr(
            sync_mod, "find_superseded_sync_branches", lambda *a, **k: [],
        )
        monkeypatch.setattr(sync_mod, "_git", lambda *a, **k: _ok())

        assert sync_mod.prune_superseded_sync_branches(repo, dry_run=False) == []
        assert seen["closed"] == [] and seen["deleted"] == []


class TestBranchListing:
    """``_sync_branch_names`` strips the remote prefix and ignores git failure."""

    def test_git_failure_yields_no_names(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hypergumbo_tracker.sync as sync_mod

        monkeypatch.setattr(sync_mod, "_git", lambda *a, **k: _fail())
        assert sync_mod._sync_branch_names(repo, "origin") == []

    def test_remote_prefix_is_stripped(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hypergumbo_tracker.sync as sync_mod

        monkeypatch.setattr(
            sync_mod, "_git",
            lambda *a, **k: _ok("origin/tracker-sync/a\norigin/tracker-sync/b\n"),
        )
        assert sync_mod._sync_branch_names(repo, "origin") == [
            "tracker-sync/a", "tracker-sync/b",
        ]


def _ok(stdout: str = "") -> object:
    import subprocess

    return subprocess.CompletedProcess([], 0, stdout, "")


def _fail() -> object:
    import subprocess

    return subprocess.CompletedProcess([], 1, "", "boom")


class TestPruneCLI:
    """``tracker sync --prune`` — the operator-facing surface.

    ``--prune`` runs BEFORE preflight on purpose, and that is the behaviour most
    worth pinning: a superseded branch is by definition one whose ops already
    reached the base, so there is typically nothing local to sync when pruning is
    wanted. Gating it on ``changed_files`` would make the command a no-op exactly
    when it is needed.
    """

    def _run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: list[str],
        pruned: list[str],
    ) -> int:
        import subprocess as sp

        import hypergumbo_tracker.sync as sync_mod
        from hypergumbo_tracker.cli import main

        monkeypatch.setattr(
            sync_mod, "prune_superseded_sync_branches", lambda *a, **k: pruned,
        )
        monkeypatch.setattr(
            sp, "run",
            lambda *a, **k: sp.CompletedProcess([], 0, str(tmp_path), ""),
        )

        def _boom(*_a: object, **_k: object) -> None:  # pragma: no cover
            raise AssertionError("preflight must not run under --prune")

        monkeypatch.setattr(sync_mod, "preflight_check", _boom)
        with pytest.raises(SystemExit) as exc:
            main(argv)
        return int(exc.value.code or 0)

    def test_dry_run_lists_branches_without_running_preflight(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        code = self._run(
            monkeypatch, tmp_path, ["sync", "--prune", "--dry-run"],
            ["tracker-sync/20260824-160016"],
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "would close 1" in out
        assert "tracker-sync/20260824-160016" in out

    def test_acting_says_closed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        code = self._run(
            monkeypatch, tmp_path, ["sync", "--prune"],
            ["tracker-sync/a", "tracker-sync/b"],
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "closed 2" in out and "would close" not in out

    def test_nothing_superseded_is_reported_plainly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        code = self._run(monkeypatch, tmp_path, ["sync", "--prune"], [])
        assert code == 0
        assert "no superseded tracker-sync branches" in capsys.readouterr().out


class TestAGenuineConflictIsNotSupersession:
    """A branch that CONFLICTS is not thereby prunable — the safety property.

    This is the distinction the whole feature rests on. Every stale sync branch
    conflicts on the forge; only some carry nothing new. ``merge-tree`` exits
    non-zero on a real conflict, and that exit must mean "cannot decide, leave it
    alone", never "superseded". Exercised with a conflict in a NON-``.ops`` file,
    because ``.ops`` paths carry ``merge=union`` and cannot conflict on a client.
    """

    def test_conflicting_branch_is_left_alone(self, repo: Path) -> None:
        (repo / "shared.txt").write_text("from dev\n")
        _commit_all(repo, "dev writes shared.txt")

        _git(repo, "checkout", "-q", "-b", "tracker-sync/20260903-000000", "HEAD~1")
        (repo / "shared.txt").write_text("from branch\n")
        _commit_all(repo, "branch writes shared.txt differently")
        _git(repo, "checkout", "-q", "dev")

        found = find_superseded_sync_branches(repo, base_branch="dev", remote="")
        assert "tracker-sync/20260903-000000" not in found

    def test_the_conflict_is_real(self, repo: Path) -> None:
        """Guards the test above from passing for the wrong reason."""
        (repo / "shared.txt").write_text("from dev\n")
        _commit_all(repo, "dev writes shared.txt")
        _git(repo, "checkout", "-q", "-b", "tracker-sync/20260903-000000", "HEAD~1")
        (repo / "shared.txt").write_text("from branch\n")
        _commit_all(repo, "branch writes shared.txt differently")
        _git(repo, "checkout", "-q", "dev")

        merged = _git(
            repo, "merge-tree", "--write-tree", "dev",
            "tracker-sync/20260903-000000", check=False,
        )
        assert merged.returncode != 0, "fixture must actually conflict"


class TestEmptyBaseTreeYieldsNothing:
    """``rev-parse`` succeeding with empty output must not read as a match.

    Unreachable through the CLI, but the comparison below it is a string equality
    and an empty ``want`` would make every branch look superseded — the one way
    this predicate could turn into a mass PR-closer.
    """

    def test_blank_base_tree_prunes_nothing(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hypergumbo_tracker.sync as sync_mod

        monkeypatch.setattr(sync_mod, "_git", lambda *a, **k: _ok("   \n"))
        assert sync_mod.find_superseded_sync_branches(repo, "dev", "") == []


class TestCredentialsCanBeSupplied:
    """``do_sync`` already holds resolved credentials; discovery must not re-run.

    One fact, one home: a second ``_load_env`` / ``_detect_api_base`` pass is a
    second chance to disagree with the first, and this module has been bitten by
    exactly that shape before.
    """

    def test_supplied_credentials_skip_discovery(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hypergumbo_tracker.sync as sync_mod

        used: dict[str, str] = {}

        def _boom(*_a: object, **_k: object) -> None:  # pragma: no cover
            raise AssertionError("discovery ran despite supplied credentials")

        monkeypatch.setattr(sync_mod, "_load_env", _boom)
        monkeypatch.setattr(sync_mod, "_detect_api_base", _boom)
        monkeypatch.setattr(sync_mod, "_find_open_pr", lambda *a, **k: None)
        monkeypatch.setattr(
            sync_mod, "_delete_remote_branch",
            lambda a, t, b: used.update(api=a, token=t) or True,
        )
        monkeypatch.setattr(
            sync_mod, "find_superseded_sync_branches", lambda *a, **k: ["tracker-sync/x"],
        )
        monkeypatch.setattr(sync_mod, "_git", lambda *a, **k: _ok())

        sync_mod.prune_superseded_sync_branches(
            repo, dry_run=False, api_base="https://supplied", token="TOK",
        )
        assert used == {"api": "https://supplied", "token": "TOK"}


class TestPruneCannotFailASync:
    """A merged sync must stay merged even if litter collection explodes.

    By the time the prune runs, the ops are already on the base. A forge hiccup
    says nothing about them, so the call is wrapped and any exception is reported
    rather than raised. ASSERTED STRUCTURALLY, and deliberately so: the property
    is POSITIONAL (after the success branch, inside a try), and driving
    ``_maybe_auto_sync`` through a full mocked push-poll-merge cycle to observe an
    ordering would test the mocks more than the code. An earlier draft here
    asserted that a raising stub raises, which is a tautology; it was deleted
    rather than kept for the count.

    IT LIVES IN ``_maybe_auto_sync``, NOT ``do_sync``. The first cut put it on
    do_sync's post-merge path and broke 23 existing tests, which script an exact
    sequence of ``_git`` calls and got StopIteration from the extra fetch. The
    safety property never depended on do_sync's sync gate: a concurrent sync
    pushes ops dev does not have, so its branch cannot satisfy "merging changes
    nothing". The predicate carries the guarantee.
    """

    def test_do_sync_no_longer_prunes(self) -> None:
        """do_sync implements a transaction; litter collection is not part of it."""
        import inspect

        import hypergumbo_tracker.sync as sync_mod

        assert "prune_superseded_sync_branches(" not in inspect.getsource(
            sync_mod.do_sync
        )

    def test_auto_sync_prunes_only_after_a_successful_sync(self) -> None:
        """Ordering is the safety property, not an implementation detail."""
        import inspect

        import hypergumbo_tracker.cli as cli_mod

        src = inspect.getsource(cli_mod._maybe_auto_sync)
        call_at = src.index("prune_superseded_sync_branches(")
        assert src.index("if sync_result.success:") < call_at
        assert src[:call_at].rindex("try:") < call_at
        assert "prune skipped" in src[call_at:]


class TestAutoSyncPruneBranches:
    """The two ``_maybe_auto_sync`` branches, driven through the real function.

    Structural tests pin WHERE the prune sits; these pin what it DOES — that a
    successful collection is reported, and that a failing one is swallowed. The
    second is the non-fatal guarantee, and it is worth a real test rather than a
    ``pragma: no cover`` because it is the property that keeps litter collection
    from being able to damage a sync that already merged.
    """

    def _drive(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        prune: object,
    ) -> None:
        import subprocess as sp

        import hypergumbo_tracker.sync as sync_mod
        from hypergumbo_tracker.cli import _maybe_auto_sync
        from hypergumbo_tracker.sync import PreflightResult, SyncResult

        monkeypatch.setenv("TRACKER_AUTO_SYNC_THRESHOLD", "10")
        (tmp_path / ".git").mkdir(exist_ok=True)
        monkeypatch.setattr(
            sp, "run",
            lambda *a, **k: sp.CompletedProcess([], 0, str(tmp_path), ""),
        )
        monkeypatch.setattr(sync_mod, "pending_sync_lines", lambda _r: 15)
        monkeypatch.setattr(
            sync_mod, "preflight_check",
            lambda _r: PreflightResult(
                ok=True, repo_root=tmp_path, git_dir=tmp_path / ".git",
                original_branch="dev",
                changed_files=[".agent/tracker/.ops/.WI-x.ops"],
                api_base="https://api/x", forgejo_user="u", forgejo_token="t",
            ),
        )
        monkeypatch.setattr(
            sync_mod, "do_sync",
            lambda **k: SyncResult(
                success=True, pr_number=99, files_synced=1, exit_code=0,
            ),
        )
        monkeypatch.setattr(sync_mod, "prune_superseded_sync_branches", prune)
        _maybe_auto_sync(tmp_path)

    def test_a_successful_prune_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._drive(
            monkeypatch, tmp_path,
            lambda *a, **k: ["tracker-sync/a", "tracker-sync/b"],
        )
        err = capsys.readouterr().err
        assert "PR #99" in err, "the sync itself must still be reported"
        assert "closed 2 superseded" in err

    def test_a_failing_prune_is_swallowed_and_the_sync_still_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _explode(*_a: object, **_k: object) -> list[str]:
            raise RuntimeError("forge down")

        self._drive(monkeypatch, tmp_path, _explode)
        err = capsys.readouterr().err
        assert "prune skipped (forge down)" in err
        assert "PR #99" in err, "a prune failure must not unreport the merge"
        assert "sync failed" not in err, "and must not open the circuit breaker"

    def test_nothing_to_prune_prints_no_prune_line(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self._drive(monkeypatch, tmp_path, lambda *a, **k: [])
        err = capsys.readouterr().err
        assert "PR #99" in err
        assert "superseded" not in err
