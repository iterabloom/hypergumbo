# SPDX-License-Identifier: MPL-2.0
"""INV-rugid: TUI preferences are per-(user, repo) state, kept by the user.

They lived at ``<tracker_root>/tracker-workspace/tui_preferences.json`` -- inside
the shared, agent-owned checkout: one copy, gitignored, not journalled, removed
by ``git clean -xdf``, and shared by every account on the checkout. The
2026-10-25 premise check found the shared copy worse than "denied by mode 600":
``_load_tui_preferences`` swallows the PermissionError and returns defaults, and
``_save_tui_preferences`` replaces the file through a temp file in the
group-writable directory, so a second user's first save REPLACED the owner's
file and the two then clobbered each other on every save.

The file now lives at ``$XDG_STATE_HOME/hypergumbo-tracker/<repo-id>/``, with
``<repo-id>`` the key the ops journal uses for the same clone, so each user has
their own and the checkout holds none. An existing in-repo file is read once
when the new one is absent and the next save writes only the new location.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_tracker import journal
from hypergumbo_tracker.tui import (
    _load_tui_preferences,
    _save_tui_preferences,
    load_tui_preferences_migrating,
    tui_preferences_path,
)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "clone"
    (root / ".git").mkdir(parents=True)
    tracker_root = root / ".agent"
    (tracker_root / "tracker-workspace").mkdir(parents=True)
    return tracker_root


def test_the_file_lives_under_the_users_state_home_keyed_like_the_journal(
        tmp_path: Path) -> None:
    tracker_root = _repo(tmp_path)
    state = tmp_path / "state"
    path = tui_preferences_path(tracker_root, environ={"XDG_STATE_HOME": str(state)})
    repo_id = journal._repo_id(tracker_root.parent.resolve())
    assert path == state / "hypergumbo-tracker" / repo_id / "tui_preferences.json"
    assert tracker_root.parent not in path.parents


def test_the_default_state_home_is_the_xdg_default(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    path = tui_preferences_path(_repo(tmp_path), environ={})
    assert path.is_relative_to(tmp_path / "home" / ".local" / "state" / "hypergumbo-tracker")


def test_two_users_on_one_checkout_get_two_files(tmp_path: Path) -> None:
    tracker_root = _repo(tmp_path)
    a = tui_preferences_path(tracker_root, environ={"XDG_STATE_HOME": str(tmp_path / "a")})
    b = tui_preferences_path(tracker_root, environ={"XDG_STATE_HOME": str(tmp_path / "b")})
    assert a != b
    _save_tui_preferences(a, {"done"}, ["A"])
    _save_tui_preferences(b, {"wont_do"}, ["B"])
    assert _load_tui_preferences(a)["hidden_statuses"] == ["done"]
    assert _load_tui_preferences(b)["hidden_statuses"] == ["wont_do"]


def test_two_clones_get_two_files(tmp_path: Path) -> None:
    env = {"XDG_STATE_HOME": str(tmp_path / "state")}
    assert (tui_preferences_path(_repo(tmp_path / "one"), environ=env)
            != tui_preferences_path(_repo(tmp_path / "two"), environ=env))


def test_a_tracker_root_outside_git_still_gets_a_stable_key(tmp_path: Path) -> None:
    root = tmp_path / "nogit" / ".agent"
    root.mkdir(parents=True)
    env = {"XDG_STATE_HOME": str(tmp_path / "state")}
    assert tui_preferences_path(root, environ=env) == tui_preferences_path(root, environ=env)


def test_an_existing_in_repo_file_is_read_when_the_new_one_is_absent(tmp_path: Path) -> None:
    tracker_root = _repo(tmp_path)
    legacy = tracker_root / "tracker-workspace" / "tui_preferences.json"
    _save_tui_preferences(legacy, {"done"}, ["X"])
    new = tmp_path / "state" / "tui_preferences.json"
    prefs = load_tui_preferences_migrating(new, legacy)
    assert prefs["hidden_statuses"] == ["done"]
    assert _load_tui_preferences(new)["hidden_statuses"] == ["done"], "migrated"
    assert legacy.is_file(), "the old copy is left alone, never deleted"


def test_the_new_file_wins_once_it_exists(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.json"
    new = tmp_path / "new.json"
    _save_tui_preferences(legacy, {"done"}, [])
    _save_tui_preferences(new, {"wont_do"}, [])
    assert load_tui_preferences_migrating(new, legacy)["hidden_statuses"] == ["wont_do"]


def test_no_file_anywhere_is_the_defaults_and_writes_nothing(tmp_path: Path) -> None:
    new = tmp_path / "state" / "p.json"
    prefs = load_tui_preferences_migrating(new, tmp_path / "absent.json")
    assert prefs == _load_tui_preferences(tmp_path / "also-absent.json")
    assert not new.exists()
