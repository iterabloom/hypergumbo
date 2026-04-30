# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``.agent/hooks/_shared/check_audit_cadence.py``.

Named to match the hook source so ``top_level_test_map.py`` selects
this file when the hook changes (per-PR smart-test would otherwise
silently skip pytest because the hook lives outside ``packages/*/src``).

Companion tests for ``scripts/concept-audit-record`` live in
``test_concept_audit_record.py``; both files use the same import pattern
because the hook reads the state file the recording script writes,
but each is independently runnable.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_PATH = REPO_ROOT / ".agent" / "hooks" / "_shared" / "check_audit_cadence.py"


def _load(path: Path, name: str) -> Any:
    """Load a Python source file as a module regardless of extension."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


hook = _load(HOOK_PATH, "check_audit_cadence")


# --- load_threshold ---

def test_load_threshold_returns_default_when_file_missing(tmp_path: Path):
    missing = tmp_path / "no-such.yaml"
    assert hook.load_threshold(missing) == hook.DEFAULT_THRESHOLD


def test_load_threshold_reads_value_from_yaml(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("concept_audit:\n  commit_threshold: 99\n")
    assert hook.load_threshold(cfg) == 99


def test_load_threshold_returns_default_when_node_missing(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("other_section:\n  threshold: 5\n")
    assert hook.load_threshold(cfg) == hook.DEFAULT_THRESHOLD


def test_load_threshold_returns_default_when_key_missing(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("concept_audit:\n  other_knob: 1\n")
    assert hook.load_threshold(cfg) == hook.DEFAULT_THRESHOLD


def test_load_threshold_returns_default_on_oserror(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("concept_audit:\n  commit_threshold: 50\n")
    cfg.chmod(0o000)
    try:
        assert hook.load_threshold(cfg) == hook.DEFAULT_THRESHOLD
    finally:
        cfg.chmod(0o644)


def test_load_threshold_returns_default_on_value_error(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("concept_audit:\n  commit_threshold: not-an-int\n")
    assert hook.load_threshold(cfg) == hook.DEFAULT_THRESHOLD


def test_load_threshold_handles_non_dict_root(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("- just\n- a\n- list\n")
    assert hook.load_threshold(cfg) == hook.DEFAULT_THRESHOLD


def test_load_threshold_handles_empty_file(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("")
    assert hook.load_threshold(cfg) == hook.DEFAULT_THRESHOLD


# --- load_state ---

def test_load_state_returns_none_when_missing(tmp_path: Path):
    assert hook.load_state(tmp_path / "no-such.json") is None


def test_load_state_reads_json(tmp_path: Path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps({"last_audit_sha": "abc", "audits_run": 1})
    )
    state = hook.load_state(state_path)
    assert state == {"last_audit_sha": "abc", "audits_run": 1}


def test_load_state_returns_none_on_corrupt_json(tmp_path: Path):
    state_path = tmp_path / "state.json"
    state_path.write_text("not valid json {")
    assert hook.load_state(state_path) is None


# --- commits_since ---

def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=path, check=True,
    )


def _commit(path: Path, filename: str, content: str, message: str) -> str:
    fpath = path / filename
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(content)
    subprocess.run(["git", "add", filename], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=path, check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path, check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_commits_since_counts_correctly(tmp_path: Path):
    _init_repo(tmp_path)
    sha0 = _commit(tmp_path, "a.txt", "1", "first")
    _commit(tmp_path, "b.txt", "2", "second")
    _commit(tmp_path, "c.txt", "3", "third")
    assert hook.commits_since(sha0, tmp_path) == 2


def test_commits_since_excludes_tracker_ops_paths(tmp_path: Path):
    _init_repo(tmp_path)
    sha0 = _commit(tmp_path, "a.txt", "1", "first")
    _commit(tmp_path, "b.txt", "2", "second")
    _commit(tmp_path, "c.txt", "3", "third")
    _commit(
        tmp_path,
        ".agent/tracker/.ops/x.ops",
        "ops",
        "tracker auto-sync",
    )
    _commit(
        tmp_path,
        ".agent/tracker-workspace/.ops/y.ops",
        "ops",
        "tracker workspace auto-sync",
    )
    # Two real commits (b.txt, c.txt) plus two tracker auto-syncs;
    # exclusion pathspec drops the latter, so count is 2.
    assert hook.commits_since(sha0, tmp_path) == 2


def test_commits_since_returns_negative_on_invalid_sha(tmp_path: Path):
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    assert hook.commits_since("not-a-real-sha", tmp_path) == -1


def test_commits_since_returns_negative_on_oserror(tmp_path: Path):
    # Non-git directory triggers git rev-list failure.
    assert hook.commits_since("HEAD", tmp_path) == -1


def test_commits_since_returns_negative_on_subprocess_oserror():
    with patch.object(
        hook.subprocess, "run", side_effect=OSError("boom"),
    ):
        assert hook.commits_since("anything") == -1


def test_commits_since_returns_negative_on_unparseable_output(tmp_path: Path):
    _init_repo(tmp_path)
    sha0 = _commit(tmp_path, "a.txt", "1", "first")

    class _FakeResult:
        returncode = 0
        stdout = "not a number\n"

    with patch.object(hook.subprocess, "run", return_value=_FakeResult()):
        assert hook.commits_since(sha0, tmp_path) == -1


# --- has_dirty_tree ---

def test_has_dirty_tree_false_on_clean_repo(tmp_path: Path):
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    assert hook.has_dirty_tree(tmp_path) is False


def test_has_dirty_tree_true_on_uncommitted_change(tmp_path: Path):
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    (tmp_path / "a.txt").write_text("modified")
    assert hook.has_dirty_tree(tmp_path) is True


def test_has_dirty_tree_ignores_tracker_ops(tmp_path: Path):
    """Mirror production: tracked .ops files modified in-place.

    The filter targets the production case where tracker auto-syncs
    leave modified-but-tracked .ops files in the working tree.
    """
    _init_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    _commit(tmp_path, ".agent/tracker/.ops/x.ops", "v1", "seed ops")
    _commit(
        tmp_path, ".agent/tracker-workspace/.ops/y.ops", "v1", "seed ws ops",
    )
    (tmp_path / ".agent" / "tracker" / ".ops" / "x.ops").write_text("v2")
    (tmp_path / ".agent" / "tracker-workspace" / ".ops" / "y.ops").write_text(
        "v2",
    )
    assert hook.has_dirty_tree(tmp_path) is False


def test_has_dirty_tree_returns_false_on_subprocess_error():
    with patch.object(
        hook.subprocess, "run", side_effect=OSError("boom"),
    ):
        assert hook.has_dirty_tree() is False


def test_has_dirty_tree_returns_false_on_nonzero_exit(tmp_path: Path):
    # Non-git dir — git status exits non-zero.
    assert hook.has_dirty_tree(tmp_path) is False


# --- build_message ---

def test_build_message_clean_tree_includes_record_command():
    msg = hook.build_message(
        commits=80, threshold=72, suspect="Foo.bar",
        last_date="2026-04-29", dirty=False,
    )
    assert "80 commits" in msg
    assert "Threshold: 72" in msg
    assert "Foo.bar" in msg
    assert "2026-04-29" in msg
    assert "scripts/concept-audit-record" in msg
    assert "defer" not in msg.lower()


def test_build_message_dirty_tree_includes_defer_language():
    msg = hook.build_message(
        commits=100, threshold=72, suspect="Foo.bar",
        last_date="2026-04-29", dirty=True,
    )
    assert "defer" in msg.lower()
    assert "uncommitted" in msg.lower()
    assert "scripts/concept-audit-record" not in msg


def test_build_message_handles_empty_suspect():
    msg = hook.build_message(
        commits=100, threshold=72, suspect="",
        last_date="", dirty=False,
    )
    assert "unrecorded domain" in msg
    assert "unknown date" in msg


# --- bootstrap state file smoke test ---

def test_state_file_at_repo_root_is_valid_json():
    """The committed bootstrap state file must always parse cleanly."""
    state_path = REPO_ROOT / ".agent" / ".last_concept_audit.json"
    if not state_path.exists():
        pytest.skip("state file not present yet")
    state = json.loads(state_path.read_text())
    assert isinstance(state.get("last_audit_sha"), str)
    assert isinstance(state.get("suspect_domain"), str)
    assert isinstance(state.get("audits_run"), int)
