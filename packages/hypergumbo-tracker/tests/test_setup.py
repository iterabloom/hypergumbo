# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.setup — idempotent setup wizard.

Covers all 20 checks in all reachable states (ok, fixed, warn, error),
the top-level run_setup() orchestration, and CLI integration via _cmd_setup.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from hypergumbo_tracker.cli import EXIT_SUCCESS, EXIT_USER_ERROR, main
from hypergumbo_tracker.setup import (
    TRACKER_CONCEPTS,
    CheckResult,
    _check_actor_resolution,
    _check_agents_md,
    _check_autonomous_mode,
    _check_config_drift,
    _check_config_ownership,
    _check_config_template,
    _check_config_validation,
    _check_config_yaml,
    _check_directory_structure,
    _check_existing_data,
    _check_git_repo,
    _check_gitattributes,
    _check_gitignore,
    _check_group_permissions,
    _check_ops_writable,
    _check_precommit_hook,
    _check_reflection_state,
    _check_stop_hook,
    _check_textconv,
    _check_tracker_wrapper,
    _ensure_safe_directory,
    format_results,
    generate_human_shim,
    results_to_json,
    run_setup,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_dir(tmp_path: Path) -> Path:
    """Create a minimal .agent/ directory (no subdirs)."""
    root = tmp_path / ".agent"
    root.mkdir()
    return root


def _make_full_agent_dir(tmp_path: Path) -> Path:
    """Create a fully populated .agent/ directory structure."""
    root = tmp_path / ".agent"
    (root / "tracker" / ".ops").mkdir(parents=True)
    (root / "tracker-workspace" / ".ops").mkdir(parents=True)
    (root / "tracker-workspace" / "stealth").mkdir(parents=True)
    return root


def _write_config_template(root: Path) -> None:
    """Write a valid config.yaml.template."""
    from helpers import make_test_config_dict

    template = root / "tracker" / "config.yaml.template"
    template.write_text(yaml.dump(make_test_config_dict()))


def _write_config(root: Path, raw: dict[str, Any] | None = None) -> None:
    """Write a config.yaml (valid by default)."""
    if raw is None:
        from helpers import make_test_config_dict

        raw = make_test_config_dict()
    (root / "tracker").mkdir(parents=True, exist_ok=True)
    (root / "tracker" / "config.yaml").write_text(yaml.dump(raw))


# ---------------------------------------------------------------------------
# Check #1: Git repo
# ---------------------------------------------------------------------------


class TestCheckGitRepo:
    """Tests for _check_git_repo (check #1)."""

    def test_no_git_repo(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        root.mkdir()
        result, repo_root = _check_git_repo(root)
        assert result.status == "warn"
        assert result.name == "git_repo"
        assert repo_root is None
        assert "not inside" in result.message.lower()

    def test_git_repo_found(self, tmp_path: Path) -> None:
        # Create a .git directory
        (tmp_path / ".git").mkdir()
        root = tmp_path / ".agent"
        root.mkdir()
        result, repo_root = _check_git_repo(root)
        assert result.status == "ok"
        assert repo_root == tmp_path

    def test_git_worktree(self, tmp_path: Path) -> None:
        # Simulate worktree: .git is a file
        (tmp_path / ".git").write_text("gitdir: /some/other/path")
        root = tmp_path / ".agent"
        root.mkdir()
        result, repo_root = _check_git_repo(root)
        assert result.status == "ok"
        assert repo_root == tmp_path


# ---------------------------------------------------------------------------
# Check #2: Directory structure
# ---------------------------------------------------------------------------


class TestCheckDirectoryStructure:
    """Tests for _check_directory_structure (check #2)."""

    def test_creates_all_dirs(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        # Don't create root yet — let the check do it
        result = _check_directory_structure(root)
        assert result.status == "fixed"
        assert result.name == "directory_structure"
        assert (root / "tracker" / ".ops").is_dir()
        assert (root / "tracker-workspace" / ".ops").is_dir()
        assert (root / "tracker-workspace" / "stealth").is_dir()

    def test_all_exist(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        result = _check_directory_structure(root)
        assert result.status == "ok"

    def test_partial_dirs(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        (root / "tracker").mkdir(parents=True)
        # Missing .ops, workspace, etc.
        result = _check_directory_structure(root)
        assert result.status == "fixed"
        assert "Created" in result.message


# ---------------------------------------------------------------------------
# Check #3: .gitattributes
# ---------------------------------------------------------------------------


class TestCheckGitattributes:
    """Tests for _check_gitattributes (check #3)."""

    def test_creates_missing(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        result = _check_gitattributes(root)
        assert result.status == "fixed"
        content = (root / "tracker" / ".ops" / ".gitattributes").read_text()
        assert "*.ops merge=union" in content

    def test_already_present(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        for ops_dir in [
            root / "tracker" / ".ops",
            root / "tracker-workspace" / ".ops",
        ]:
            (ops_dir / ".gitattributes").write_text("*.ops merge=union\n")
        result = _check_gitattributes(root)
        assert result.status == "ok"

    def test_appends_to_existing(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        ga = root / "tracker" / ".ops" / ".gitattributes"
        ga.write_text("# some existing content")
        # Workspace one is fine
        (root / "tracker-workspace" / ".ops" / ".gitattributes").write_text(
            "*.ops merge=union\n"
        )
        result = _check_gitattributes(root)
        assert result.status == "fixed"
        content = ga.read_text()
        assert "# some existing content" in content
        assert "*.ops merge=union" in content

    def test_appends_with_trailing_newline(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        ga = root / "tracker" / ".ops" / ".gitattributes"
        ga.write_text("# existing\n")
        (root / "tracker-workspace" / ".ops" / ".gitattributes").write_text(
            "*.ops merge=union\n"
        )
        result = _check_gitattributes(root)
        assert result.status == "fixed"
        content = ga.read_text()
        # Should not have double newlines
        assert "# existing\n*.ops merge=union\n" == content


# ---------------------------------------------------------------------------
# Check #4: .gitignore
# ---------------------------------------------------------------------------


class TestCheckGitignore:
    """Tests for _check_gitignore (check #4)."""

    def test_creates_missing(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        result = _check_gitignore(root)
        assert result.status == "fixed"
        assert "config.yaml" in (root / "tracker" / ".gitignore").read_text()
        assert "*.ops" in (
            root / "tracker-workspace" / "stealth" / ".gitignore"
        ).read_text()

    def test_already_present(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        (root / "tracker" / ".gitignore").write_text("config.yaml\n")
        (root / "tracker-workspace" / "stealth" / ".gitignore").write_text("*.ops\n")
        result = _check_gitignore(root)
        assert result.status == "ok"

    def test_appends_missing_line(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        gi = root / "tracker" / ".gitignore"
        gi.write_text("other-stuff")
        (root / "tracker-workspace" / "stealth" / ".gitignore").write_text("*.ops\n")
        result = _check_gitignore(root)
        assert result.status == "fixed"
        content = gi.read_text()
        assert "other-stuff" in content
        assert "config.yaml" in content


# ---------------------------------------------------------------------------
# Check #5: config.yaml.template
# ---------------------------------------------------------------------------


class TestCheckConfigTemplate:
    """Tests for _check_config_template (check #5)."""

    def test_exists(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config_template(root)
        result = _check_config_template(root)
        assert result.status == "ok"

    def test_missing(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        result = _check_config_template(root)
        assert result.status == "warn"
        assert "not found" in result.message


# ---------------------------------------------------------------------------
# Check #6: config.yaml
# ---------------------------------------------------------------------------


class TestCheckConfigYaml:
    """Tests for _check_config_yaml (check #6)."""

    def test_exists_valid(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(root)
        result = _check_config_yaml(root)
        assert result.status == "ok"

    def test_missing_with_template(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config_template(root)
        result = _check_config_yaml(root)
        assert result.status == "fixed"
        assert "copied from template" in result.message
        assert (root / "tracker" / "config.yaml").exists()

    def test_missing_no_template(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        result = _check_config_yaml(root)
        assert result.status == "warn"
        assert "not found" in result.message

    def test_unparseable_with_template(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config_template(root)
        (root / "tracker" / "config.yaml").write_text("{{invalid yaml!!")
        result = _check_config_yaml(root)
        assert result.status == "fixed"
        assert ".old" in result.message
        assert (root / "tracker" / "config.yaml.old").exists()
        # New config should be from template
        assert (root / "tracker" / "config.yaml").exists()

    def test_unparseable_no_template(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        (root / "tracker" / "config.yaml").write_text("{{invalid yaml!!")
        result = _check_config_yaml(root)
        assert result.status == "warn"
        assert ".old" in result.message
        assert (root / "tracker" / "config.yaml.old").exists()

    def test_empty_file(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config_template(root)
        (root / "tracker" / "config.yaml").write_text("")
        result = _check_config_yaml(root)
        assert result.status == "fixed"
        assert ".old" in result.message

    def test_non_dict_yaml(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config_template(root)
        (root / "tracker" / "config.yaml").write_text("- a list\n- not a mapping\n")
        result = _check_config_yaml(root)
        assert result.status == "fixed"
        assert ".old" in result.message


# ---------------------------------------------------------------------------
# Check #7: Config validation
# ---------------------------------------------------------------------------


class TestCheckConfigValidation:
    """Tests for _check_config_validation (check #7)."""

    def test_no_config(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        result = _check_config_validation(root)
        assert result.status == "ok"
        assert "skipped" in result.message

    def test_valid_config(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(root)
        result = _check_config_validation(root)
        assert result.status == "ok"

    def test_invalid_config(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(root, {"statuses": []})  # Invalid: empty statuses
        result = _check_config_validation(root)
        assert result.status == "error"
        assert "failed" in result.message.lower()

    def test_non_dict_config(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        (root / "tracker" / "config.yaml").write_text("42\n")
        result = _check_config_validation(root)
        assert result.status == "error"
        assert "not a YAML mapping" in result.message

    def test_yaml_error(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        (root / "tracker" / "config.yaml").write_text("{{broken yaml")
        result = _check_config_validation(root)
        assert result.status == "error"
        assert "YAML parse error" in result.message


# ---------------------------------------------------------------------------
# Check #8: Config drift
# ---------------------------------------------------------------------------


class TestCheckConfigDrift:
    """Tests for _check_config_drift (check #8)."""

    def test_no_config(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        result = _check_config_drift(root)
        assert result.status == "ok"
        assert "skipped" in result.message

    def test_no_template(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(root)
        result = _check_config_drift(root)
        assert result.status == "ok"

    def test_matching(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(root)
        _write_config_template(root)
        result = _check_config_drift(root)
        assert result.status == "ok"
        assert "matches" in result.message

    def test_drift_detected(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(
            root,
            {
                "kinds": {
                    "invariant": {"prefix": "INV"},
                    "custom_kind": {"prefix": "CK"},
                },
                "statuses": ["todo_hard", "done"],
                "stop_hook": {
                    "blocking_statuses": ["todo_hard"],
                    "resolved_statuses": ["done"],
                },
                "lamport_branches": ["dev"],
            },
        )
        _write_config_template(root)
        result = _check_config_drift(root)
        assert result.status == "warn"
        assert "drift" in result.message.lower()

    def test_yaml_error_skipped(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        (root / "tracker" / "config.yaml").write_text("{{bad yaml")
        _write_config_template(root)
        result = _check_config_drift(root)
        assert result.status == "ok"
        assert "skipped" in result.message


# ---------------------------------------------------------------------------
# Check #9: Actor resolution
# ---------------------------------------------------------------------------


class TestCheckActorResolution:
    """Tests for _check_actor_resolution (check #9)."""

    def test_human_user(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(root)
        with patch(
            "hypergumbo_tracker.setup.resolve_actor", return_value=("human", "alice")
        ):
            result = _check_actor_resolution(root)
        assert result.status == "ok"
        assert "alice" in result.message

    def test_agent_user(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(root)
        with patch(
            "hypergumbo_tracker.setup.resolve_actor",
            return_value=("agent", "bot_agent"),
        ):
            result = _check_actor_resolution(root)
        assert result.status == "warn"
        assert "bot_agent" in result.message

    def test_no_config(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        with patch(
            "hypergumbo_tracker.setup.resolve_actor", return_value=("human", "alice")
        ):
            result = _check_actor_resolution(root)
        assert result.status == "ok"

    def test_yaml_error_fallback(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        (root / "tracker" / "config.yaml").write_text("{{bad yaml")
        with patch(
            "hypergumbo_tracker.setup.resolve_actor", return_value=("human", "alice")
        ):
            result = _check_actor_resolution(root)
        assert result.status == "ok"

    def test_custom_patterns(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(
            root,
            {
                "kinds": {"work_item": {"prefix": "WI"}},
                "statuses": ["todo_hard", "done"],
                "stop_hook": {
                    "blocking_statuses": ["todo_hard"],
                    "resolved_statuses": ["done"],
                },
                "actor_resolution": {"agent_usernames": ["ci_*"]},
                "lamport_branches": ["dev"],
            },
        )
        with patch(
            "hypergumbo_tracker.setup.resolve_actor", return_value=("human", "alice")
        ) as mock_resolve:
            result = _check_actor_resolution(root)
            mock_resolve.assert_called_once_with(["ci_*"])
        assert result.status == "ok"

    def test_non_list_agent_usernames_fallback(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(
            root,
            {
                "kinds": {"work_item": {"prefix": "WI"}},
                "statuses": ["todo_hard", "done"],
                "stop_hook": {
                    "blocking_statuses": ["todo_hard"],
                    "resolved_statuses": ["done"],
                },
                "actor_resolution": {"agent_usernames": "not-a-list"},
                "lamport_branches": ["dev"],
            },
        )
        with patch(
            "hypergumbo_tracker.setup.resolve_actor", return_value=("human", "alice")
        ) as mock_resolve:
            result = _check_actor_resolution(root)
            # Falls back to default patterns
            mock_resolve.assert_called_once_with(["*_agent"])

    def test_non_dict_actor_resolution(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(
            root,
            {
                "kinds": {"work_item": {"prefix": "WI"}},
                "statuses": ["todo_hard", "done"],
                "stop_hook": {
                    "blocking_statuses": ["todo_hard"],
                    "resolved_statuses": ["done"],
                },
                "actor_resolution": "not-a-dict",
                "lamport_branches": ["dev"],
            },
        )
        with patch(
            "hypergumbo_tracker.setup.resolve_actor", return_value=("human", "alice")
        ) as mock_resolve:
            result = _check_actor_resolution(root)
            # Falls back to default patterns
            mock_resolve.assert_called_once_with(["*_agent"])

    def test_empty_agent_usernames_fallback(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(
            root,
            {
                "kinds": {"work_item": {"prefix": "WI"}},
                "statuses": ["todo_hard", "done"],
                "stop_hook": {
                    "blocking_statuses": ["todo_hard"],
                    "resolved_statuses": ["done"],
                },
                "actor_resolution": {"agent_usernames": []},
                "lamport_branches": ["dev"],
            },
        )
        with patch(
            "hypergumbo_tracker.setup.resolve_actor", return_value=("human", "alice")
        ) as mock_resolve:
            result = _check_actor_resolution(root)
            mock_resolve.assert_called_once_with(["*_agent"])


# ---------------------------------------------------------------------------
# Check #10: Config ownership
# ---------------------------------------------------------------------------


class TestCheckConfigOwnership:
    """Tests for _check_config_ownership (check #10)."""

    def test_no_config(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        root.mkdir()
        result = _check_config_ownership(root)
        assert result.status == "ok"
        assert "skipped" in result.message

    def _make_with_config(self, tmp_path: Path) -> Path:
        """Helper: create .agent dir with config.yaml files."""
        root = _make_full_agent_dir(tmp_path)
        (root / "tracker" / "config.yaml").write_text("statuses: []")
        (root / "tracker-workspace" / "config.yaml").write_text("statuses: []")
        return root

    def test_owned_by_human(self, tmp_path: Path) -> None:
        root = self._make_with_config(tmp_path)
        with patch(
            "hypergumbo_tracker.setup.resolve_actor",
            return_value=("human", "jgstern"),
        ):
            result = _check_config_ownership(root)
        assert result.status == "ok"

    def test_agent_owns_config_warns(self, tmp_path: Path) -> None:
        root = self._make_with_config(tmp_path)
        with patch(
            "hypergumbo_tracker.setup.resolve_actor",
            return_value=("agent", "myproject_agent"),
        ):
            result = _check_config_ownership(root)
        # Agent owns the file (same uid), so it should warn
        assert result.status == "warn"
        assert "agent" in result.message

    def test_agent_different_owner_ok(self, tmp_path: Path) -> None:
        root = self._make_with_config(tmp_path)
        config = root / "tracker" / "config.yaml"
        # Simulate config owned by a different uid (human)
        real_stat = config.stat()
        fake_stat = MagicMock()
        fake_stat.st_uid = real_stat.st_uid + 1  # Different from current uid
        with (
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("agent", "myproject_agent"),
            ),
            patch.object(Path, "stat", return_value=fake_stat),
        ):
            result = _check_config_ownership(root)
        assert result.status == "ok"

    def test_human_fixes_wrong_owner(self, tmp_path: Path) -> None:
        root = self._make_with_config(tmp_path)
        config = root / "tracker" / "config.yaml"
        real_stat = config.stat()
        fake_stat = MagicMock()
        fake_stat.st_uid = real_stat.st_uid + 1  # Different from current uid
        with (
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("human", "jgstern"),
            ),
            patch.object(Path, "stat", return_value=fake_stat),
            patch("hypergumbo_tracker.setup.os.chown") as mock_chown,
        ):
            result = _check_config_ownership(root)
        assert result.status == "fixed"
        assert mock_chown.called

    def test_invalid_yaml_config(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        (root / "tracker" / "config.yaml").write_text(": bad yaml {{{")
        (root / "tracker-workspace" / "config.yaml").write_text("statuses: []")
        with patch(
            "hypergumbo_tracker.setup.resolve_actor",
            return_value=("agent", "myproject_agent"),
        ):
            result = _check_config_ownership(root)
        # Should still work with default patterns
        assert result.status == "warn"

    def test_human_chown_fails(self, tmp_path: Path) -> None:
        root = self._make_with_config(tmp_path)
        config = root / "tracker" / "config.yaml"
        real_stat = config.stat()
        fake_stat = MagicMock()
        fake_stat.st_uid = real_stat.st_uid + 1
        with (
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("human", "jgstern"),
            ),
            patch.object(Path, "stat", return_value=fake_stat),
            patch(
                "hypergumbo_tracker.setup.os.chown",
                side_effect=OSError("permission denied"),
            ),
        ):
            result = _check_config_ownership(root)
        assert result.status == "warn"
        assert "sudo" in result.details[1]


# ---------------------------------------------------------------------------
# Check #11: Group permissions
# ---------------------------------------------------------------------------


class TestCheckGroupPermissions:
    """Tests for _check_group_permissions (check #11)."""

    def test_no_ops_dirs(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        root.mkdir()

        result = _check_group_permissions(root)
        assert result.status == "ok"
        assert "skipped" in result.message

    def test_single_user_setup(self, tmp_path: Path) -> None:
        """When .ops group is the user's primary group, it's single-user."""
        root = _make_full_agent_dir(tmp_path)

        # Default — dirs are owned by the user's primary group
        result = _check_group_permissions(root)
        assert result.status == "ok"
        assert "Single-user" in result.message

    def test_shared_group_correct(self, tmp_path: Path) -> None:
        """Shared group with correct permissions passes."""
        import grp
        import stat

        root = _make_full_agent_dir(tmp_path)
        ops_dir = root / "tracker" / ".ops"

        # Find a group we're actually in (other than primary)
        current_gid = os.stat(ops_dir).st_gid
        fake_gid = current_gid + 1  # Simulate a shared group

        fake_stat = MagicMock()
        fake_stat.st_gid = fake_gid
        fake_stat.st_mode = stat.S_IRWXU | stat.S_IRWXG | stat.S_ISGID
        fake_stat.st_uid = os.getuid()

        fake_grp = MagicMock()
        fake_grp.gr_name = "project-dev"
        fake_grp.gr_mem = [os.environ.get("USER", "testuser")]

        with (
            patch.object(Path, "stat", return_value=fake_stat),
            patch("hypergumbo_tracker.setup.grp.getgrgid", return_value=fake_grp),
            patch(
                "hypergumbo_tracker.setup.pwd.getpwuid",
                return_value=MagicMock(
                    pw_name=os.environ.get("USER", "testuser"),
                    pw_gid=current_gid,
                ),
            ),
        ):
            result = _check_group_permissions(root)
        assert result.status == "ok"
        assert "correct" in result.message

    def test_missing_group_write(self, tmp_path: Path) -> None:
        """Shared group without group-write is an error."""
        import stat

        root = _make_full_agent_dir(tmp_path)
        current_gid = os.stat(root / "tracker" / ".ops").st_gid
        fake_gid = current_gid + 1

        fake_stat = MagicMock()
        fake_stat.st_gid = fake_gid
        fake_stat.st_mode = stat.S_IRWXU | stat.S_IRGRP  # No group write
        fake_stat.st_uid = os.getuid()

        fake_grp = MagicMock()
        fake_grp.gr_name = "project-dev"
        fake_grp.gr_mem = [os.environ.get("USER", "testuser")]

        with (
            patch.object(Path, "stat", return_value=fake_stat),
            patch("hypergumbo_tracker.setup.grp.getgrgid", return_value=fake_grp),
            patch(
                "hypergumbo_tracker.setup.pwd.getpwuid",
                return_value=MagicMock(
                    pw_name=os.environ.get("USER", "testuser"),
                    pw_gid=current_gid,
                ),
            ),
        ):
            result = _check_group_permissions(root)
        assert result.status == "error"
        assert "group-write" in str(result.details)
        # Fix command uses actual group name, not placeholder
        assert "sudo chgrp -R project-dev" in str(result.details)

    def test_user_not_in_group(self, tmp_path: Path) -> None:
        """User not in the shared group is an error."""
        import stat

        root = _make_full_agent_dir(tmp_path)
        current_gid = os.stat(root / "tracker" / ".ops").st_gid
        fake_gid = current_gid + 1

        fake_stat = MagicMock()
        fake_stat.st_gid = fake_gid
        fake_stat.st_mode = stat.S_IRWXU | stat.S_IRWXG | stat.S_ISGID
        fake_stat.st_uid = os.getuid()

        fake_grp = MagicMock()
        fake_grp.gr_name = "project-dev"
        fake_grp.gr_mem = ["someone_else"]  # Current user not in group

        with (
            patch.object(Path, "stat", return_value=fake_stat),
            patch("hypergumbo_tracker.setup.grp.getgrgid", return_value=fake_grp),
            patch(
                "hypergumbo_tracker.setup.pwd.getpwuid",
                return_value=MagicMock(
                    pw_name="testuser",
                    pw_gid=current_gid,
                ),
            ),
        ):
            result = _check_group_permissions(root)
        assert result.status == "error"
        assert "not in group" in str(result.details)

    def test_unknown_gid(self, tmp_path: Path) -> None:
        """Directory owned by a gid with no group entry."""
        root = _make_full_agent_dir(tmp_path)
        current_gid = os.stat(root / "tracker" / ".ops").st_gid
        fake_gid = current_gid + 1

        fake_stat = MagicMock()
        fake_stat.st_gid = fake_gid
        fake_stat.st_mode = 0
        fake_stat.st_uid = os.getuid()

        with (
            patch.object(Path, "stat", return_value=fake_stat),
            patch(
                "hypergumbo_tracker.setup.grp.getgrgid",
                side_effect=KeyError("unknown gid"),
            ),
            patch(
                "hypergumbo_tracker.setup.pwd.getpwuid",
                return_value=MagicMock(
                    pw_name="testuser",
                    pw_gid=current_gid,
                ),
            ),
        ):
            result = _check_group_permissions(root)
        assert result.status == "error"
        assert "unknown gid" in str(result.details)
        # Falls back to placeholder when group name can't be resolved
        assert "sudo chgrp -R GROUP" in str(result.details)

    def test_group_member_lookup_keyerror(self, tmp_path: Path) -> None:
        """KeyError when looking up group members is caught silently."""
        import stat

        root = _make_full_agent_dir(tmp_path)
        current_gid = os.stat(root / "tracker" / ".ops").st_gid
        fake_gid = current_gid + 1

        fake_stat = MagicMock()
        fake_stat.st_gid = fake_gid
        fake_stat.st_mode = stat.S_IRWXU | stat.S_IRWXG | stat.S_ISGID
        fake_stat.st_uid = os.getuid()

        fake_grp_name_only = MagicMock()
        fake_grp_name_only.gr_name = "project-dev"

        def getgrgid_side_effect(gid):
            # First call per dir gets name, second gets members
            if not hasattr(getgrgid_side_effect, "_calls"):
                getgrgid_side_effect._calls = 0
            getgrgid_side_effect._calls += 1
            # Odd calls return name, even calls raise KeyError
            if getgrgid_side_effect._calls % 2 == 1:
                return fake_grp_name_only
            raise KeyError("no such gid")

        with (
            patch.object(Path, "stat", return_value=fake_stat),
            patch(
                "hypergumbo_tracker.setup.grp.getgrgid",
                side_effect=getgrgid_side_effect,
            ),
            patch(
                "hypergumbo_tracker.setup.pwd.getpwuid",
                return_value=MagicMock(
                    pw_name="testuser",
                    pw_gid=current_gid,
                ),
            ),
        ):
            result = _check_group_permissions(root)
        # KeyError on member lookup is silently caught — no member
        # check problems added, permissions are correct, so it passes.
        assert result.status == "ok"

    def test_git_dirs_checked_when_repo_root_given(self, tmp_path: Path) -> None:
        """When repo_root is given, .git/ subdirs are included in checks."""
        import stat

        root = _make_full_agent_dir(tmp_path)
        repo_root = tmp_path
        git_dir = repo_root / ".git"
        for sub in ("refs/heads", "refs/tags", "objects", "logs"):
            (git_dir / sub).mkdir(parents=True)

        current_gid = os.stat(root / "tracker" / ".ops").st_gid
        fake_gid = current_gid + 1

        # .ops dirs are fine (correct group), but .git dirs are wrong
        real_ops = {
            str(root / "tracker" / ".ops"),
            str(root / "tracker-workspace"),
            str(root / "tracker-workspace" / ".ops"),
            str(root / "tracker-workspace" / "stealth"),
        }

        good_stat = MagicMock()
        good_stat.st_gid = fake_gid
        good_stat.st_mode = (
            stat.S_IFDIR | stat.S_IRWXU | stat.S_IRWXG | stat.S_ISGID
        )
        good_stat.st_uid = os.getuid()

        bad_stat = MagicMock()
        bad_stat.st_gid = fake_gid
        bad_stat.st_mode = (
            stat.S_IFDIR | stat.S_IRWXU | stat.S_IRGRP  # No group write
        )
        bad_stat.st_uid = os.getuid()

        original_stat = Path.stat

        def selective_stat(self, *args, **kwargs):
            if str(self) in real_ops:
                return good_stat
            if ".git" in str(self):
                return bad_stat
            return original_stat(self, *args, **kwargs)

        fake_grp = MagicMock()
        fake_grp.gr_name = "project-dev"
        fake_grp.gr_mem = [os.environ.get("USER", "testuser")]

        with (
            patch.object(Path, "stat", selective_stat),
            patch("hypergumbo_tracker.setup.grp.getgrgid", return_value=fake_grp),
            patch(
                "hypergumbo_tracker.setup.pwd.getpwuid",
                return_value=MagicMock(
                    pw_name=os.environ.get("USER", "testuser"),
                    pw_gid=current_gid,
                ),
            ),
        ):
            result = _check_group_permissions(root, repo_root=repo_root)
        assert result.status == "error"
        assert "group-write" in str(result.details)
        # Fix commands include .git/ paths
        assert "sudo chgrp -R project-dev .git/" in str(result.details)
        assert "sudo chmod -R g+w .git/" in str(result.details)
        assert "sudo find .git/ -type d -exec chmod g+s" in str(result.details)

    def test_git_dirs_skipped_without_repo_root(self, tmp_path: Path) -> None:
        """Without repo_root, .git/ dirs are not checked."""
        import stat

        root = _make_full_agent_dir(tmp_path)
        # Create .git dirs that would fail — but repo_root is None
        git_dir = tmp_path / ".git"
        for sub in ("refs/heads", "refs/tags", "objects", "logs"):
            (git_dir / sub).mkdir(parents=True)

        current_gid = os.stat(root / "tracker" / ".ops").st_gid
        fake_gid = current_gid + 1

        fake_stat = MagicMock()
        fake_stat.st_gid = fake_gid
        fake_stat.st_mode = stat.S_IRWXU | stat.S_IRWXG | stat.S_ISGID
        fake_stat.st_uid = os.getuid()

        fake_grp = MagicMock()
        fake_grp.gr_name = "project-dev"
        fake_grp.gr_mem = [os.environ.get("USER", "testuser")]

        with (
            patch.object(Path, "stat", return_value=fake_stat),
            patch("hypergumbo_tracker.setup.grp.getgrgid", return_value=fake_grp),
            patch(
                "hypergumbo_tracker.setup.pwd.getpwuid",
                return_value=MagicMock(
                    pw_name=os.environ.get("USER", "testuser"),
                    pw_gid=current_gid,
                ),
            ),
        ):
            result = _check_group_permissions(root)  # No repo_root
        # Only .ops dirs checked, all fine → passes
        assert result.status == "ok"


# ---------------------------------------------------------------------------
# Check #12: .ops/ writable
# ---------------------------------------------------------------------------


class TestCheckOpsWritable:
    """Tests for _check_ops_writable (check #12)."""

    def test_all_writable(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        result = _check_ops_writable(root)
        assert result.status == "ok"

    def test_not_writable(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        ops_dir = root / "tracker" / ".ops"
        with patch("hypergumbo_tracker.setup.os.access", return_value=False):
            result = _check_ops_writable(root)
        assert result.status == "error"

    def test_missing_dirs_ignored(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        root.mkdir()
        # No .ops dirs exist
        result = _check_ops_writable(root)
        assert result.status == "ok"


# ---------------------------------------------------------------------------
# Check #11: Git textconv
# ---------------------------------------------------------------------------


class TestCheckTextconv:
    """Tests for _check_textconv (check #11)."""

    def test_no_git_repo(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        result = _check_textconv(root, None)
        assert result.status == "ok"
        assert "skipped" in result.message

    def test_already_configured(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        # Create .gitattributes with both lines
        for ops_dir in [
            root / "tracker" / ".ops",
            root / "tracker-workspace" / ".ops",
        ]:
            (ops_dir / ".gitattributes").write_text(
                "*.ops merge=union\n*.ops diff=tracker-ops\n"
            )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "python -m hypergumbo_tracker.cli textconv\n"
        with (
            patch("hypergumbo_tracker.setup._ensure_safe_directory", return_value=False),
            patch("hypergumbo_tracker.setup.subprocess.run", return_value=mock_result),
        ):
            result = _check_textconv(root, tmp_path)
        assert result.status == "ok"

    def test_driver_not_set(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        # Pre-populate .gitattributes with diff line so only the driver is fixed
        for ops_dir in [
            root / "tracker" / ".ops",
            root / "tracker-workspace" / ".ops",
        ]:
            (ops_dir / ".gitattributes").write_text("*.ops diff=tracker-ops\n")

        call_count = 0

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # First call: check config — not set
                result.returncode = 1
                result.stdout = ""
            else:
                # Second call: set config — success
                result.returncode = 0
                result.stdout = ""
            return result

        with (
            patch("hypergumbo_tracker.setup._ensure_safe_directory", return_value=False),
            patch("hypergumbo_tracker.setup.subprocess.run", side_effect=mock_run),
        ):
            result = _check_textconv(root, tmp_path)
        assert result.status == "fixed"

    def test_git_command_fails(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        with (
            patch("hypergumbo_tracker.setup._ensure_safe_directory", return_value=False),
            patch(
                "hypergumbo_tracker.setup.subprocess.run",
                side_effect=FileNotFoundError("git not found"),
            ),
        ):
            result = _check_textconv(root, tmp_path)
        assert result.status == "warn"
        assert "could not configure" in result.message.lower()

    def test_git_config_set_fails(self, tmp_path: Path) -> None:
        """Local and global config both fail — falls through to warn."""
        root = _make_full_agent_dir(tmp_path)

        call_count = 0

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Check config — not set
                result = MagicMock()
                result.returncode = 1
                result.stdout = ""
                return result
            # Both local and global set fail
            raise subprocess.CalledProcessError(1, cmd)

        with (
            patch("hypergumbo_tracker.setup._ensure_safe_directory", return_value=False),
            patch("hypergumbo_tracker.setup.subprocess.run", side_effect=mock_run),
        ):
            result = _check_textconv(root, tmp_path)
        assert result.status == "warn"

    def test_diff_line_created(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        # Driver already configured, but no diff line in .gitattributes
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "python -m hypergumbo_tracker.cli textconv\n"
        with (
            patch("hypergumbo_tracker.setup._ensure_safe_directory", return_value=False),
            patch("hypergumbo_tracker.setup.subprocess.run", return_value=mock_result),
        ):
            result = _check_textconv(root, tmp_path)
        assert result.status == "fixed"
        for ops_dir in [
            root / "tracker" / ".ops",
            root / "tracker-workspace" / ".ops",
        ]:
            content = (ops_dir / ".gitattributes").read_text()
            assert "*.ops diff=tracker-ops" in content

    def test_diff_line_appended_to_existing(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        ga = root / "tracker" / ".ops" / ".gitattributes"
        ga.write_text("*.ops merge=union")
        (root / "tracker-workspace" / ".ops" / ".gitattributes").write_text(
            "*.ops merge=union\n*.ops diff=tracker-ops\n"
        )
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "textconv-driver\n"
        with (
            patch("hypergumbo_tracker.setup._ensure_safe_directory", return_value=False),
            patch("hypergumbo_tracker.setup.subprocess.run", return_value=mock_result),
        ):
            result = _check_textconv(root, tmp_path)
        assert result.status == "fixed"
        content = ga.read_text()
        assert "*.ops merge=union" in content
        assert "*.ops diff=tracker-ops" in content

    def test_safe_directory_auto_fixed(self, tmp_path: Path) -> None:
        """safe.directory is auto-added, then textconv is configured."""
        root = _make_full_agent_dir(tmp_path)
        for ops_dir in [
            root / "tracker" / ".ops",
            root / "tracker-workspace" / ".ops",
        ]:
            (ops_dir / ".gitattributes").write_text("*.ops diff=tracker-ops\n")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "python -m hypergumbo_tracker.cli textconv\n"
        with (
            patch("hypergumbo_tracker.setup._ensure_safe_directory", return_value=True),
            patch("hypergumbo_tracker.setup.subprocess.run", return_value=mock_result),
        ):
            result = _check_textconv(root, tmp_path)
        # safe.directory auto-fix counts as a fix even when textconv was ok
        assert result.status == "fixed"
        assert "1 fix" in result.message

    def test_local_config_not_writable_falls_back_to_global(
        self, tmp_path: Path
    ) -> None:
        """Local git config not writable — falls back to --global."""
        root = _make_full_agent_dir(tmp_path)
        for ops_dir in [
            root / "tracker" / ".ops",
            root / "tracker-workspace" / ".ops",
        ]:
            (ops_dir / ".gitattributes").write_text("*.ops diff=tracker-ops\n")

        call_count = 0

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Check config — not set
                result = MagicMock()
                result.returncode = 1
                result.stdout = ""
                return result
            if call_count == 2:
                # Local config write fails
                raise subprocess.CalledProcessError(1, cmd)
            # Global config write succeeds
            result = MagicMock()
            result.returncode = 0
            return result

        with (
            patch("hypergumbo_tracker.setup._ensure_safe_directory", return_value=False),
            patch("hypergumbo_tracker.setup.subprocess.run", side_effect=mock_run),
        ):
            result = _check_textconv(root, tmp_path)
        assert result.status == "fixed"


class TestEnsureSafeDirectory:
    """Tests for _ensure_safe_directory."""

    def test_no_dubious_ownership(self, tmp_path: Path) -> None:
        """Repo is trusted — no fix needed."""
        mock_result = MagicMock()
        mock_result.stderr = ""
        with patch("hypergumbo_tracker.setup.subprocess.run", return_value=mock_result):
            assert _ensure_safe_directory(tmp_path) is False

    def test_dubious_ownership_fixed(self, tmp_path: Path) -> None:
        """Repo has dubious ownership — safe.directory is added."""
        call_count = 0

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                # git status — dubious ownership
                result.stderr = (
                    "fatal: detected dubious ownership in repository at '/foo'"
                )
            else:
                # git config --global --add safe.directory — success
                result.returncode = 0
            return result

        with patch("hypergumbo_tracker.setup.subprocess.run", side_effect=mock_run):
            assert _ensure_safe_directory(tmp_path) is True

    def test_dubious_ownership_fix_fails(self, tmp_path: Path) -> None:
        """Repo has dubious ownership but git config fails."""
        call_count = 0

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                result = MagicMock()
                result.stderr = (
                    "fatal: detected dubious ownership in repository at '/foo'"
                )
                return result
            raise subprocess.CalledProcessError(1, cmd)

        with patch("hypergumbo_tracker.setup.subprocess.run", side_effect=mock_run):
            assert _ensure_safe_directory(tmp_path) is False

    def test_git_not_found(self, tmp_path: Path) -> None:
        """git binary not found — returns False."""
        with patch(
            "hypergumbo_tracker.setup.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            assert _ensure_safe_directory(tmp_path) is False


# ---------------------------------------------------------------------------
# Check #12: Existing data
# ---------------------------------------------------------------------------


class TestCheckExistingData:
    """Tests for _check_existing_data (check #12)."""

    def test_no_ops_files(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        result = _check_existing_data(root)
        assert result.status == "ok"
        assert "no existing data" in result.message.lower()

    def test_valid_ops(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(root)
        # Create a valid .ops file
        ops_dir = root / "tracker" / ".ops"
        ops_file = ops_dir / ".test-id.ops"
        ops_file.write_text(
            textwrap.dedent("""\
            - op: create  # abc1
              at: "2026-01-01T00:00:00Z"  # abc2
              by: agent  # abc3
              actor: test_agent  # abc4
              clock: 1  # abc5
              nonce: a1b2  # abc6
              data:  # abc7
                kind: work_item  # abc8
                title: "Test item"  # abc9
                status: todo_hard  # abc10
                priority: 2  # abc11
            """)
        )
        from hypergumbo_tracker.validation import ValidationResult

        with patch(
            "hypergumbo_tracker.setup.validate_all",
            return_value=ValidationResult(),
        ):
            result = _check_existing_data(root)
        assert result.status == "ok"
        assert "validates cleanly" in result.message.lower()

    def test_ops_with_errors(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        ops_file = root / "tracker" / ".ops" / ".test-id.ops"
        ops_file.write_text("not valid ops content\n")
        from hypergumbo_tracker.validation import ValidationResult

        vr = ValidationResult(errors=["bad data"], warnings=["minor issue"])
        with patch("hypergumbo_tracker.setup.validate_all", return_value=vr):
            result = _check_existing_data(root)
        assert result.status == "error"
        assert "1 error" in result.message

    def test_ops_with_warnings_only(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        ops_file = root / "tracker" / ".ops" / ".test-id.ops"
        ops_file.write_text("content\n")
        from hypergumbo_tracker.validation import ValidationResult

        vr = ValidationResult(warnings=["minor issue"])
        with patch("hypergumbo_tracker.setup.validate_all", return_value=vr):
            result = _check_existing_data(root)
        assert result.status == "warn"

    def test_many_errors_truncated(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        ops_file = root / "tracker" / ".ops" / ".test-id.ops"
        ops_file.write_text("content\n")
        from hypergumbo_tracker.validation import ValidationResult

        vr = ValidationResult(errors=[f"error {i}" for i in range(15)])
        with patch("hypergumbo_tracker.setup.validate_all", return_value=vr):
            result = _check_existing_data(root)
        assert result.status == "error"
        assert any("and 5 more" in d for d in result.details)

    def test_many_warnings_truncated(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        ops_file = root / "tracker" / ".ops" / ".test-id.ops"
        ops_file.write_text("content\n")
        from hypergumbo_tracker.validation import ValidationResult

        vr = ValidationResult(warnings=[f"warn {i}" for i in range(15)])
        with patch("hypergumbo_tracker.setup.validate_all", return_value=vr):
            result = _check_existing_data(root)
        assert result.status == "warn"
        assert any("and 5 more" in d for d in result.details)


# ---------------------------------------------------------------------------
# Check #13: Tracker wrapper
# ---------------------------------------------------------------------------


class TestCheckTrackerWrapper:
    """Tests for _check_tracker_wrapper (check #13)."""

    def test_no_repo(self) -> None:
        result = _check_tracker_wrapper(None)
        assert result.status == "ok"
        assert "skipped" in result.message

    def test_wrapper_exists_and_executable(self, tmp_path: Path) -> None:
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        wrapper = scripts / "tracker"
        wrapper.write_text("#!/bin/bash\nexec htrac \"$@\"")
        wrapper.chmod(0o755)
        result = _check_tracker_wrapper(tmp_path)
        assert result.status == "ok"
        assert "found" in result.message

    def test_wrapper_exists_not_executable(self, tmp_path: Path) -> None:
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        wrapper = scripts / "tracker"
        wrapper.write_text("#!/bin/bash\nexec htrac \"$@\"")
        wrapper.chmod(0o644)
        result = _check_tracker_wrapper(tmp_path)
        assert result.status == "warn"
        assert "not executable" in result.message

    def test_wrapper_missing(self, tmp_path: Path) -> None:
        result = _check_tracker_wrapper(tmp_path)
        assert result.status == "warn"
        assert "not found" in result.message
        assert any("scripts/tracker" in d for d in result.details)


# ---------------------------------------------------------------------------
# Check #14: AGENTS.md
# ---------------------------------------------------------------------------


class TestCheckAgentsMd:
    """Tests for _check_agents_md (check #14)."""

    def test_no_repo(self) -> None:
        result = _check_agents_md(None)
        assert result.status == "ok"
        assert "skipped" in result.message

    def test_no_agents_md(self, tmp_path: Path) -> None:
        result = _check_agents_md(tmp_path)
        assert result.status == "warn"
        assert "found" in result.message.lower()

    def test_all_concepts_present(self, tmp_path: Path) -> None:
        content = textwrap.dedent("""\
        # Agent Instructions
        Use `scripts/tracker show <ID>` to read state.
        Don't read .ops files — they pollute context.
        Use `tracker ready` to pick tasks.
        tracker: commit prefix for tracker-only changes.
        Assume structural until proven otherwise.
        Name the invariant before fixing.
        Follow TDD: Red, Green, Refactor.
        Write a failing test first.
        Must maintain 100% coverage with cov-fail-under.
        Batch tracker operations into fewer commits.
        Use needs_human_review for governance proposals.
        """)
        (tmp_path / "AGENTS.md").write_text(content)
        result = _check_agents_md(tmp_path)
        assert result.status == "ok"

    def test_missing_concepts(self, tmp_path: Path) -> None:
        content = "# Agent Instructions\nNothing relevant here.\n"
        (tmp_path / "AGENTS.md").write_text(content)
        result = _check_agents_md(tmp_path)
        assert result.status == "warn"
        assert str(len(TRACKER_CONCEPTS)) in result.message

    def test_partial_concepts(self, tmp_path: Path) -> None:
        content = "# Agent Instructions\nUse tracker ready for tasks.\n"
        (tmp_path / "AGENTS.md").write_text(content)
        result = _check_agents_md(tmp_path)
        assert result.status == "warn"
        expected_missing = len(TRACKER_CONCEPTS) - 1
        assert str(expected_missing) in result.message

    def test_claude_md_fallback(self, tmp_path: Path) -> None:
        content = textwrap.dedent("""\
        tracker show <ID> for state.
        .ops files pollute context.
        tracker ready for tasks.
        tracker: prefix for commits.
        Assume structural until proven otherwise.
        Red, Green, Refactor cycle.
        100% coverage requirement.
        Batch tracker operations.
        Use needs_human_review for human-judgment items.
        """)
        (tmp_path / "CLAUDE.md").write_text(content)
        result = _check_agents_md(tmp_path)
        assert result.status == "ok"

    def test_agents_md_preferred_over_claude_md(self, tmp_path: Path) -> None:
        # AGENTS.md has nothing, CLAUDE.md has everything
        (tmp_path / "AGENTS.md").write_text("nothing here\n")
        (tmp_path / "CLAUDE.md").write_text(
            "tracker show <ID>\ntracker ready\ntracker: prefix\n"
        )
        result = _check_agents_md(tmp_path)
        # Should use AGENTS.md (first match), so concepts missing
        assert result.status == "warn"


# ---------------------------------------------------------------------------
# Check #15: Stop hook
# ---------------------------------------------------------------------------


class TestCheckStopHook:
    """Tests for _check_stop_hook (check #15)."""

    def test_no_repo(self) -> None:
        result = _check_stop_hook(None)
        assert result.status == "ok"
        assert "skipped" in result.message

    def test_no_hooks_dir(self, tmp_path: Path) -> None:
        result = _check_stop_hook(tmp_path)
        assert result.status == "warn"
        assert "no stop hook" in result.message.lower()

    def test_stop_hook_with_tracker(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".agent" / "hooks" / "pre-stop"
        hooks_dir.mkdir(parents=True)
        stop_script = hooks_dir / "stop.sh"
        stop_script.write_text("#!/bin/bash\nhtrac count-todos\nhtrac hash-todos\n")
        result = _check_stop_hook(tmp_path)
        assert result.status == "ok"

    def test_stop_hook_without_tracker(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".agent" / "hooks" / "pre-stop"
        hooks_dir.mkdir(parents=True)
        stop_script = hooks_dir / "stop.sh"
        stop_script.write_text("#!/bin/bash\necho 'no tracker'\n")
        result = _check_stop_hook(tmp_path)
        assert result.status == "warn"
        assert "does not reference" in result.message

    def test_tracker_commands_in_sourced_file(self, tmp_path: Path) -> None:
        """Commands in a sibling file (e.g. stop_logic.sh) should be found."""
        hooks_dir = tmp_path / ".agent" / "hooks"
        vendor_dir = hooks_dir / "claude-code"
        vendor_dir.mkdir(parents=True)
        shared_dir = hooks_dir / "_shared"
        shared_dir.mkdir(parents=True)
        # Vendor stop.sh sources shared logic but has no tracker commands
        (vendor_dir / "stop.sh").write_text(
            '#!/bin/bash\nsource "$DIR/../_shared/stop_logic.sh"\n'
        )
        # Shared stop_logic.sh has the actual tracker commands
        (shared_dir / "stop_logic.sh").write_text(
            '#!/bin/bash\nscripts/tracker count-todos\nscripts/tracker hash-todos\n'
        )
        result = _check_stop_hook(tmp_path)
        assert result.status == "ok"

    def test_githooks_stop(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".githooks"
        hooks_dir.mkdir()
        stop_script = hooks_dir / "stop.sh"
        stop_script.write_text("#!/bin/bash\nhtrac guidance\n")
        result = _check_stop_hook(tmp_path)
        assert result.status == "ok"

    def test_unreadable_stop_hook(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".agent" / "hooks" / "pre-stop"
        hooks_dir.mkdir(parents=True)
        stop_script = hooks_dir / "stop.sh"
        stop_script.write_text("content")

        orig_read_text = Path.read_text

        def patched_read_text(self_path, *a, **kw):
            if self_path == stop_script:
                raise OSError("permission denied")
            return orig_read_text(self_path, *a, **kw)

        with patch.object(Path, "read_text", patched_read_text):
            result = _check_stop_hook(tmp_path)
        assert result.status == "warn"
        assert "no stop hook" in result.message.lower()


# ---------------------------------------------------------------------------
# Check #16: Pre-commit hook
# ---------------------------------------------------------------------------


class TestCheckPrecommitHook:
    """Tests for _check_precommit_hook (check #16)."""

    def test_no_repo(self) -> None:
        result = _check_precommit_hook(None)
        assert result.status == "ok"
        assert "skipped" in result.message

    def test_no_hook(self, tmp_path: Path) -> None:
        result = _check_precommit_hook(tmp_path)
        assert result.status == "warn"
        assert "no pre-commit hook" in result.message.lower()

    def test_hook_with_tracker_validate(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".githooks"
        hooks_dir.mkdir()
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/bash\nhtrac tracker validate || exit 1\n")
        result = _check_precommit_hook(tmp_path)
        assert result.status == "ok"

    def test_hook_without_tracker_validate(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".githooks"
        hooks_dir.mkdir()
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/bash\nrun-tests\n")
        result = _check_precommit_hook(tmp_path)
        assert result.status == "warn"
        assert "does not reference" in result.message

    def test_git_hooks_dir(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/bash\nhtrac tracker validate\n")
        result = _check_precommit_hook(tmp_path)
        assert result.status == "ok"

    def test_githooks_preferred(self, tmp_path: Path) -> None:
        # .githooks has the hook, .git/hooks doesn't
        hooks_dir = tmp_path / ".githooks"
        hooks_dir.mkdir()
        hook = hooks_dir / "pre-commit"
        hook.write_text("#!/bin/bash\ntracker validate\n")
        result = _check_precommit_hook(tmp_path)
        assert result.status == "ok"

    def test_unreadable_precommit_hook(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / ".githooks"
        hooks_dir.mkdir()
        hook = hooks_dir / "pre-commit"
        hook.write_text("content")

        orig_read_text = Path.read_text

        def patched_read_text(self_path, *a, **kw):
            if self_path == hook:
                raise OSError("permission denied")
            return orig_read_text(self_path, *a, **kw)

        with patch.object(Path, "read_text", patched_read_text):
            result = _check_precommit_hook(tmp_path)
        # Falls through to "no hook found" since all hooks are unreadable
        assert result.status == "warn"


# ---------------------------------------------------------------------------
# Check #19: Autonomous mode consistency
# ---------------------------------------------------------------------------


class TestCheckAutonomousMode:
    """Tests for _check_autonomous_mode (check #19)."""

    def test_no_repo(self) -> None:
        result = _check_autonomous_mode(None)
        assert result.status == "ok"
        assert "skipped" in result.message

    def test_no_autonomous_mode_file(self, tmp_path: Path) -> None:
        (tmp_path / ".agent").mkdir()
        result = _check_autonomous_mode(tmp_path)
        assert result.status == "ok"
        assert "not configured" in result.message.lower()

    def test_off_mode_no_sentinel(self, tmp_path: Path) -> None:
        (tmp_path / "AUTONOMOUS_MODE.txt").write_text("OFF\n")
        (tmp_path / ".agent").mkdir()
        result = _check_autonomous_mode(tmp_path)
        assert result.status == "ok"

    def test_off_mode_with_sentinel(self, tmp_path: Path) -> None:
        """OFF mode but LOOP sentinel present — inconsistent."""
        (tmp_path / "AUTONOMOUS_MODE.txt").write_text("OFF\n")
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "LOOP").write_text("")
        result = _check_autonomous_mode(tmp_path)
        assert result.status == "warn"
        assert "LOOP" in result.message or "inconsistent" in result.message.lower()

    def test_broad_mode_with_sentinel(self, tmp_path: Path) -> None:
        (tmp_path / "AUTONOMOUS_MODE.txt").write_text("BROAD\n")
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "LOOP").write_text("")
        result = _check_autonomous_mode(tmp_path)
        assert result.status == "ok"
        assert "BROAD" in result.message

    def test_deep_mode_without_sentinel(self, tmp_path: Path) -> None:
        """DEEP mode but no LOOP sentinel — agent won't actually loop."""
        (tmp_path / "AUTONOMOUS_MODE.txt").write_text("DEEP\n")
        (tmp_path / ".agent").mkdir()
        result = _check_autonomous_mode(tmp_path)
        assert result.status == "warn"
        assert "LOOP" in result.message or "sentinel" in result.message.lower()

    def test_true_mode_treated_as_broad(self, tmp_path: Path) -> None:
        (tmp_path / "AUTONOMOUS_MODE.txt").write_text("TRUE\n")
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "LOOP").write_text("")
        result = _check_autonomous_mode(tmp_path)
        assert result.status == "ok"

    def test_unknown_mode_value(self, tmp_path: Path) -> None:
        (tmp_path / "AUTONOMOUS_MODE.txt").write_text("YOLO\n")
        (tmp_path / ".agent").mkdir()
        result = _check_autonomous_mode(tmp_path)
        assert result.status == "warn"
        assert "unrecognized" in result.message.lower() or "YOLO" in result.message


# ---------------------------------------------------------------------------
# Check #20: Reflection state
# ---------------------------------------------------------------------------


class TestCheckReflectionState:
    """Tests for _check_reflection_state (check #20)."""

    def test_no_repo(self) -> None:
        result = _check_reflection_state(None)
        assert result.status == "ok"
        assert "skipped" in result.message

    def test_no_state_file(self, tmp_path: Path) -> None:
        (tmp_path / ".agent").mkdir()
        result = _check_reflection_state(tmp_path)
        assert result.status == "ok"
        assert "no reflection state" in result.message.lower()

    def test_valid_recent_state(self, tmp_path: Path) -> None:
        """A valid state file with a recent timestamp should be ok."""
        from datetime import datetime, timezone

        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = {
            "last_completed_utc": now,
            "branch": "dev",
            "last_pr": 42,
            "last_pr_state": "none",
            "pending_hard_todos": 0,
            "pending_soft_todos": 0,
            "notes": "",
        }
        (agent_dir / "last_stop_check.json").write_text(json.dumps(state))
        result = _check_reflection_state(tmp_path)
        assert result.status == "ok"

    def test_stale_state(self, tmp_path: Path) -> None:
        """A state file > 7 days old should warn."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        state = {
            "last_completed_utc": "2020-01-01T00:00:00Z",
            "branch": "dev",
            "last_pr": 0,
            "last_pr_state": "none",
            "pending_hard_todos": 0,
            "pending_soft_todos": 0,
            "notes": "",
        }
        (agent_dir / "last_stop_check.json").write_text(json.dumps(state))
        result = _check_reflection_state(tmp_path)
        assert result.status == "warn"
        assert "stale" in result.message.lower()

    def test_unparseable_json(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "last_stop_check.json").write_text("not json{{{")
        result = _check_reflection_state(tmp_path)
        assert result.status == "warn"
        assert "parse" in result.message.lower() or "invalid" in result.message.lower()

    def test_non_dict_json(self, tmp_path: Path) -> None:
        """JSON file that parses but isn't an object (e.g., a list)."""
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        (agent_dir / "last_stop_check.json").write_text("[1, 2, 3]")
        result = _check_reflection_state(tmp_path)
        assert result.status == "warn"
        assert "not a json object" in result.message.lower()

    def test_missing_required_keys(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        state = {"branch": "dev"}  # missing last_completed_utc
        (agent_dir / "last_stop_check.json").write_text(json.dumps(state))
        result = _check_reflection_state(tmp_path)
        assert result.status == "warn"
        assert "missing" in result.message.lower() or "key" in result.message.lower()

    def test_unparseable_timestamp(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir()
        state = {
            "last_completed_utc": "not-a-date",
            "branch": "dev",
            "last_pr": 0,
            "last_pr_state": "none",
            "pending_hard_todos": 0,
            "pending_soft_todos": 0,
            "notes": "",
        }
        (agent_dir / "last_stop_check.json").write_text(json.dumps(state))
        result = _check_reflection_state(tmp_path)
        assert result.status == "warn"
        assert "timestamp" in result.message.lower() or "parse" in result.message.lower()


# ---------------------------------------------------------------------------
# run_setup orchestration
# ---------------------------------------------------------------------------


class TestRunSetup:
    """Tests for run_setup top-level orchestrator."""

    def test_fresh_directory(self, tmp_path: Path) -> None:
        root = tmp_path / ".agent"
        with patch(
            "hypergumbo_tracker.setup.resolve_actor", return_value=("human", "alice")
        ):
            results = run_setup(root)
        # Should have one result per check (21 total, including sync prerequisites)
        assert len(results) == 21
        # Directory structure should be fixed
        dir_result = next(r for r in results if r.name == "directory_structure")
        assert dir_result.status == "fixed"

    def test_idempotent(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        _write_config(root)
        _write_config_template(root)

        # Add all gitattributes and gitignore
        for ops_dir in [
            root / "tracker" / ".ops",
            root / "tracker-workspace" / ".ops",
        ]:
            (ops_dir / ".gitattributes").write_text(
                "*.ops merge=union\n*.ops diff=tracker-ops\n"
            )
        (root / "tracker" / ".gitignore").write_text("config.yaml\n")
        (root / "tracker-workspace" / "stealth" / ".gitignore").write_text("*.ops\n")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "textconv\n"

        with (
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("human", "alice"),
            ),
            patch(
                "hypergumbo_tracker.setup.subprocess.run", return_value=mock_result
            ),
        ):
            results = run_setup(root)

        # All checks should be ok (or warn for agentic checks if no repo context)
        statuses = [r.status for r in results]
        assert "fixed" not in statuses
        assert "error" not in statuses

    def test_explicit_repo_root(self, tmp_path: Path) -> None:
        root = _make_full_agent_dir(tmp_path)
        repo = tmp_path / "my-repo"
        repo.mkdir()
        with patch(
            "hypergumbo_tracker.setup.resolve_actor", return_value=("human", "alice")
        ):
            results = run_setup(root, repo_root=repo)
        # The repo_root should be used for agentic checks
        wrapper = next(r for r in results if r.name == "tracker_wrapper")
        # wrapper checks for scripts/tracker in repo_root
        assert wrapper.name == "tracker_wrapper"


# ---------------------------------------------------------------------------
# format_results and results_to_json
# ---------------------------------------------------------------------------


class TestFormatResults:
    """Tests for output formatting functions."""

    def test_all_ok(self) -> None:
        results = [
            CheckResult(name="a", status="ok", message="All good"),
            CheckResult(name="b", status="ok", message="Fine"),
        ]
        text, exit_code = format_results(results)
        assert exit_code == 0
        assert "All checks passed" in text
        assert "[ok]" in text

    def test_with_errors(self) -> None:
        results = [
            CheckResult(name="a", status="ok", message="Good"),
            CheckResult(name="b", status="error", message="Bad", details=["fix it"]),
        ]
        text, exit_code = format_results(results)
        assert exit_code == 1
        assert "1 error" in text
        assert "[error]" in text
        assert "fix it" in text

    def test_with_fixed_and_warnings(self) -> None:
        results = [
            CheckResult(name="a", status="fixed", message="Fixed it"),
            CheckResult(name="b", status="warn", message="Watch out"),
        ]
        text, exit_code = format_results(results)
        assert exit_code == 0
        assert "1 fixed" in text
        assert "1 warning" in text

    def test_multiple_warnings_plural(self) -> None:
        results = [
            CheckResult(name="a", status="warn", message="W1"),
            CheckResult(name="b", status="warn", message="W2"),
        ]
        text, exit_code = format_results(results)
        assert "2 warnings" in text

    def test_results_to_json(self) -> None:
        results = [
            CheckResult(name="a", status="ok", message="Good"),
            CheckResult(
                name="b", status="error", message="Bad", details=["detail1"]
            ),
        ]
        data = results_to_json(results)
        assert data["summary"]["ok"] == 1
        assert data["summary"]["error"] == 1
        assert len(data["checks"]) == 2
        assert data["checks"][1]["details"] == ["detail1"]


# ---------------------------------------------------------------------------
# CLI integration (_cmd_setup)
# ---------------------------------------------------------------------------


class TestCmdSetup:
    """Tests for the setup CLI command integration."""

    def test_setup_text_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = tmp_path / ".agent"
        with (
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("human", "alice"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["--tracker-root", str(root.parent), "setup", "--root", str(root)])
        assert exc_info.value.code == EXIT_SUCCESS
        captured = capsys.readouterr()
        assert "Setup complete" in captured.out

    def test_setup_json_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = tmp_path / ".agent"
        with (
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("human", "alice"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["--json", "setup", "--root", str(root)])
        assert exc_info.value.code == EXIT_SUCCESS
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "checks" in data
        assert "summary" in data

    def test_setup_auto_detect_root(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # When no root specified and no existing tracker, falls back to cwd/.agent
        with (
            patch("hypergumbo_tracker.cli.Path.cwd", return_value=tmp_path),
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("human", "alice"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["setup"])
        assert exc_info.value.code == EXIT_SUCCESS

    def test_setup_with_errors_exit_code_1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        root = tmp_path / ".agent"
        root.mkdir()
        # Make a config that will fail validation
        (root / "tracker" / ".ops").mkdir(parents=True)
        (root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (root / "tracker-workspace" / "stealth").mkdir(parents=True)
        (root / "tracker" / "config.yaml").write_text(
            yaml.dump({"statuses": []})
        )

        with (
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("human", "alice"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["setup", "--root", str(root)])
        assert exc_info.value.code == EXIT_USER_ERROR

    def test_setup_existing_tracker_root(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Create an existing tracker at a known location, then use auto-detect
        root = tmp_path / ".agent"
        (root / "tracker" / ".ops").mkdir(parents=True)
        (root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (root / "tracker-workspace" / "stealth").mkdir(parents=True)

        with (
            patch("hypergumbo_tracker.cli._find_tracker_root", return_value=root),
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("human", "alice"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["setup"])
        assert exc_info.value.code == EXIT_SUCCESS

    def test_agent_prompt_decline(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Agent user declines to continue — prints human shim."""
        root = tmp_path / ".agent"
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        with (
            patch(
                "hypergumbo_tracker.cli.resolve_actor",
                return_value=("agent", "myproject_agent"),
            ),
            patch.object(sys, "stdin", mock_stdin),
            patch("builtins.input", return_value="n"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["setup", "--root", str(root)])
        assert exc_info.value.code == EXIT_SUCCESS
        captured = capsys.readouterr()
        # Shim is printed with copy-paste commands
        assert "htrac setup" in captured.out
        assert "htrac tui" in captured.out

    def test_agent_prompt_accept(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Agent user confirms continue — runs setup."""
        root = tmp_path / ".agent"
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        with (
            patch(
                "hypergumbo_tracker.cli.resolve_actor",
                return_value=("agent", "myproject_agent"),
            ),
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("agent", "myproject_agent"),
            ),
            patch.object(sys, "stdin", mock_stdin),
            patch("builtins.input", return_value="y"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["setup", "--root", str(root)])
        assert exc_info.value.code == EXIT_SUCCESS
        captured = capsys.readouterr()
        assert "Setup complete" in captured.out

    def test_agent_no_prompt_in_json_mode(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Agent user in JSON mode — no prompt, just runs."""
        root = tmp_path / ".agent"
        with (
            patch(
                "hypergumbo_tracker.cli.resolve_actor",
                return_value=("agent", "myproject_agent"),
            ),
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("agent", "myproject_agent"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["--json", "setup", "--root", str(root)])
        assert exc_info.value.code == EXIT_SUCCESS

    def test_agent_prompt_eof(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """EOFError on agent prompt — prints human shim."""
        root = tmp_path / ".agent"
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        with (
            patch(
                "hypergumbo_tracker.cli.resolve_actor",
                return_value=("agent", "myproject_agent"),
            ),
            patch.object(sys, "stdin", mock_stdin),
            patch("builtins.input", side_effect=EOFError),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["setup", "--root", str(root)])
        assert exc_info.value.code == EXIT_SUCCESS
        captured = capsys.readouterr()
        assert "htrac setup" in captured.out

    def test_error_override_decline(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Setup has errors, user declines to continue."""
        root = tmp_path / ".agent"
        root.mkdir()
        (root / "tracker" / ".ops").mkdir(parents=True)
        (root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (root / "tracker-workspace" / "stealth").mkdir(parents=True)
        # Invalid config triggers a validation error
        (root / "tracker" / "config.yaml").write_text(
            yaml.dump({"statuses": []})
        )
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        with (
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("human", "alice"),
            ),
            patch(
                "hypergumbo_tracker.cli.resolve_actor",
                return_value=("human", "alice"),
            ),
            patch.object(sys, "stdin", mock_stdin),
            patch("builtins.input", return_value="n"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["setup", "--root", str(root)])
        assert exc_info.value.code == EXIT_USER_ERROR
        captured = capsys.readouterr()
        assert "error(s) found" in captured.out

    def test_error_override_eof(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """EOFError on error override prompt — treats as decline."""
        root = tmp_path / ".agent"
        root.mkdir()
        (root / "tracker" / ".ops").mkdir(parents=True)
        (root / "tracker-workspace" / ".ops").mkdir(parents=True)
        (root / "tracker-workspace" / "stealth").mkdir(parents=True)
        (root / "tracker" / "config.yaml").write_text(
            yaml.dump({"statuses": []})
        )
        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        with (
            patch(
                "hypergumbo_tracker.setup.resolve_actor",
                return_value=("human", "alice"),
            ),
            patch(
                "hypergumbo_tracker.cli.resolve_actor",
                return_value=("human", "alice"),
            ),
            patch.object(sys, "stdin", mock_stdin),
            patch("builtins.input", side_effect=EOFError),
            pytest.raises(SystemExit) as exc_info,
        ):
            main(["setup", "--root", str(root)])
        assert exc_info.value.code == EXIT_USER_ERROR


# ---------------------------------------------------------------------------
# Human shim generation
# ---------------------------------------------------------------------------


class TestGenerateHumanShim:
    """Tests for generate_human_shim() — copy-paste command block."""

    def test_basic_shim(self, tmp_path: Path) -> None:
        """Minimal shim with no venv and no shared group."""
        root = tmp_path / ".agent"
        (root / "tracker" / ".ops").mkdir(parents=True)
        shim = generate_human_shim(root)
        assert f"cd {tmp_path}" in shim
        assert "htrac setup" in shim
        assert "htrac tui" in shim

    def test_shim_with_venv(self, tmp_path: Path) -> None:
        """Shim includes venv activation when .venv exists."""
        root = tmp_path / ".agent"
        (root / "tracker" / ".ops").mkdir(parents=True)
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("")
        shim = generate_human_shim(root)
        assert f"source {tmp_path / '.venv' / 'bin' / 'activate'}" in shim

    def test_shim_with_venv_dir(self, tmp_path: Path) -> None:
        """Shim detects 'venv/' as fallback when '.venv/' doesn't exist."""
        root = tmp_path / ".agent"
        (root / "tracker" / ".ops").mkdir(parents=True)
        venv = tmp_path / "venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("")
        shim = generate_human_shim(root)
        assert f"source {tmp_path / 'venv' / 'bin' / 'activate'}" in shim

    def test_shim_with_virtual_env_envvar(self, tmp_path: Path) -> None:
        """Shim uses VIRTUAL_ENV env var when no .venv/ directory."""
        root = tmp_path / ".agent"
        (root / "tracker" / ".ops").mkdir(parents=True)
        venv_dir = tmp_path / "my_env"
        (venv_dir / "bin").mkdir(parents=True)
        (venv_dir / "bin" / "activate").write_text("")
        with patch.dict(os.environ, {"VIRTUAL_ENV": str(venv_dir)}):
            shim = generate_human_shim(root)
        assert f"source {venv_dir / 'bin' / 'activate'}" in shim

    def test_shim_with_shared_group(self, tmp_path: Path) -> None:
        """Shim includes group fix commands when shared group is detected."""
        root = tmp_path / ".agent"
        ops_dir = root / "tracker" / ".ops"
        ops_dir.mkdir(parents=True)
        # Mock the group detection — set .ops dir to a non-primary gid
        with (
            patch("hypergumbo_tracker.setup.pwd.getpwuid") as mock_pwd,
            patch("hypergumbo_tracker.setup.grp.getgrgid") as mock_grp,
        ):
            mock_pwd.return_value = MagicMock(pw_gid=1000)
            mock_grp.return_value = MagicMock(gr_name="project-dev")
            # Make the .ops dir appear to have a different gid
            real_stat = os.stat(ops_dir)
            mock_stat = MagicMock()
            mock_stat.st_gid = 9999  # Different from primary gid (1000)
            mock_stat.st_mode = real_stat.st_mode
            with patch.object(Path, "stat", return_value=mock_stat):
                shim = generate_human_shim(root)
        assert "sudo chgrp -R project-dev" in shim
        assert "sudo chmod -R g+rws" in shim
        assert "newgrp project-dev" in shim

    def test_shim_unknown_group(self, tmp_path: Path) -> None:
        """Shim omits group commands when gid lookup fails."""
        root = tmp_path / ".agent"
        ops_dir = root / "tracker" / ".ops"
        ops_dir.mkdir(parents=True)
        with (
            patch("hypergumbo_tracker.setup.pwd.getpwuid") as mock_pwd,
            patch("hypergumbo_tracker.setup.grp.getgrgid", side_effect=KeyError),
        ):
            mock_pwd.return_value = MagicMock(pw_gid=1000)
            real_stat = os.stat(ops_dir)
            mock_stat = MagicMock()
            mock_stat.st_gid = 9999
            mock_stat.st_mode = real_stat.st_mode
            with patch.object(Path, "stat", return_value=mock_stat):
                shim = generate_human_shim(root)
        assert "chgrp" not in shim
        assert "newgrp" not in shim

    def test_shim_traversal_needed(self, tmp_path: Path) -> None:
        """Shim includes chmod o+rx when repo is under agent's home."""
        root = tmp_path / ".agent"
        (root / "tracker" / ".ops").mkdir(parents=True)
        home_dir = tmp_path  # Pretend this is home
        with patch("hypergumbo_tracker.setup.Path.home", return_value=home_dir):
            shim = generate_human_shim(root)
        assert f"sudo chmod o+rx {home_dir}" in shim

    def test_shim_no_traversal_when_not_under_home(self, tmp_path: Path) -> None:
        """No chmod o+rx when repo is not under agent's home."""
        root = tmp_path / ".agent"
        (root / "tracker" / ".ops").mkdir(parents=True)
        fake_home = Path("/some/other/path")
        with patch("hypergumbo_tracker.setup.Path.home", return_value=fake_home):
            shim = generate_human_shim(root)
        assert "chmod o+rx" not in shim

    def test_shim_tui_shortcut(self, tmp_path: Path) -> None:
        """TUI shortcut combines cd and htrac tui."""
        root = tmp_path / ".agent"
        (root / "tracker" / ".ops").mkdir(parents=True)
        home_dir = tmp_path
        venv = tmp_path / ".venv" / "bin"
        venv.mkdir(parents=True)
        (venv / "activate").write_text("")
        with patch("hypergumbo_tracker.setup.Path.home", return_value=home_dir):
            shim = generate_human_shim(root)
        # TUI line should chain cd, source, and htrac tui
        tui_line = next(l for l in shim.splitlines() if "htrac tui" in l)
        assert f"cd {tmp_path}" in tui_line
        assert "source" in tui_line
        assert "htrac tui" in tui_line

    def test_shim_no_ops_dirs(self, tmp_path: Path) -> None:
        """Shim works even when no .ops directories exist yet."""
        root = tmp_path / ".agent"
        root.mkdir()
        shim = generate_human_shim(root)
        assert "htrac setup" in shim
        assert "htrac tui" in shim

    def test_shim_with_git_root(self, tmp_path: Path) -> None:
        """Shim uses the git root when a .git directory exists."""
        repo = tmp_path / "myrepo"
        (repo / ".git").mkdir(parents=True)
        root = repo / ".agent"
        (root / "tracker" / ".ops").mkdir(parents=True)
        shim = generate_human_shim(root)
        assert f"cd {repo}" in shim

    def test_shim_includes_git_dir_perms(self, tmp_path: Path) -> None:
        """Shim includes .git/ permission fix commands when shared group detected."""
        repo = tmp_path / "myrepo"
        git_dir = repo / ".git"
        git_dir.mkdir(parents=True)
        root = repo / ".agent"
        ops_dir = root / "tracker" / ".ops"
        ops_dir.mkdir(parents=True)

        with (
            patch("hypergumbo_tracker.setup.pwd.getpwuid") as mock_pwd,
            patch("hypergumbo_tracker.setup.grp.getgrgid") as mock_grp,
        ):
            mock_pwd.return_value = MagicMock(pw_gid=1000)
            mock_grp.return_value = MagicMock(gr_name="project-dev")
            real_stat = os.stat(ops_dir)
            mock_stat = MagicMock()
            mock_stat.st_gid = 9999
            mock_stat.st_mode = real_stat.st_mode
            with patch.object(Path, "stat", return_value=mock_stat):
                shim = generate_human_shim(root)
        assert f"sudo chgrp -R project-dev {git_dir}" in shim
        assert f"sudo chmod -R g+w {git_dir}" in shim
        assert f"sudo find {git_dir} -type d -exec chmod g+s" in shim

    def test_shim_no_git_dir_perms_without_git(self, tmp_path: Path) -> None:
        """No .git/ perm commands when .git/ doesn't exist."""
        # Put root in a subdir where no .git/ directory exists
        project = tmp_path / "no_git_project"
        project.mkdir()
        root = project / ".agent"
        ops_dir = root / "tracker" / ".ops"
        ops_dir.mkdir(parents=True)

        original_stat = Path.stat

        def stat_for_ops_only(self, *args, **kwargs):
            """Return mock stat only for .ops dirs; real stat elsewhere."""
            if str(self) == str(ops_dir):
                mock_st = MagicMock()
                mock_st.st_gid = 9999
                mock_st.st_mode = original_stat(self).st_mode
                return mock_st
            return original_stat(self, *args, **kwargs)

        with (
            patch("hypergumbo_tracker.setup.pwd.getpwuid") as mock_pwd,
            patch("hypergumbo_tracker.setup.grp.getgrgid") as mock_grp,
            patch.object(Path, "stat", stat_for_ops_only),
        ):
            mock_pwd.return_value = MagicMock(pw_gid=1000)
            mock_grp.return_value = MagicMock(gr_name="project-dev")
            shim = generate_human_shim(root)
        # Has tracker perms but not .git/ perms
        assert "sudo chgrp -R project-dev" in shim
        assert "chmod -R g+w" not in shim  # No .git/ specific command


# ---------------------------------------------------------------------------
# Edge cases and constants
# ---------------------------------------------------------------------------


class TestTrackerConcepts:
    """Tests for TRACKER_CONCEPTS constant structure."""

    def test_all_concepts_have_required_keys(self) -> None:
        for name, concept in TRACKER_CONCEPTS.items():
            assert "description" in concept, f"{name} missing description"
            assert "patterns" in concept, f"{name} missing patterns"
            assert "suggestion" in concept, f"{name} missing suggestion"
            assert len(concept["patterns"]) > 0, f"{name} has no patterns"

    def test_patterns_are_valid_regex(self) -> None:
        import re

        for name, concept in TRACKER_CONCEPTS.items():
            for pattern in concept["patterns"]:
                try:
                    re.compile(pattern, re.IGNORECASE)
                except re.error as e:
                    pytest.fail(f"{name} pattern {pattern!r} invalid: {e}")


class TestCheckResultDataclass:
    """Tests for CheckResult dataclass."""

    def test_defaults(self) -> None:
        r = CheckResult(name="test", status="ok", message="msg")
        assert r.details == []

    def test_with_details(self) -> None:
        r = CheckResult(name="test", status="ok", message="msg", details=["d1"])
        assert r.details == ["d1"]
