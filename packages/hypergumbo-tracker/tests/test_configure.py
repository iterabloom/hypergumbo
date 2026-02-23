# SPDX-License-Identifier: MPL-2.0
"""Tests for hypergumbo_tracker.configure — interactive config editor.

Covers run_configure() with mock stdin for all interactive paths:
agent rejection, statuses editing, kinds editing, stop_hook editing,
tags editing, actor_resolution editing, lamport_branches editing,
validation failure, diff display, write confirmation, workspace
config update, and abort path.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from hypergumbo_tracker.configure import (
    _compute_diff,
    _configure_actor_resolution,
    _configure_kinds,
    _configure_lamport_branches,
    _configure_statuses,
    _configure_stop_hook,
    _configure_tags,
    _edit_list,
    run_configure,
)
from hypergumbo_tracker.setup import config_lock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_dir(tmp_path: Path) -> Path:
    """Create a minimal .agent/ directory with config."""
    root = tmp_path / ".agent"
    (root / "tracker" / ".ops").mkdir(parents=True)
    (root / "tracker-workspace" / ".ops").mkdir(parents=True)
    (root / "tracker-workspace" / "stealth").mkdir(parents=True)
    return root


def _write_config(root: Path, data: dict[str, Any] | None = None) -> Path:
    """Write a valid config.yaml and return its path."""
    if data is None:
        from helpers import make_test_config_dict

        data = make_test_config_dict()
    config_path = root / "tracker" / "config.yaml"
    config_path.write_text(yaml.dump(data))
    return config_path


def _make_input_fn(responses: list[str]) -> Any:
    """Create a mock input function that returns responses in order.

    When responses are exhausted, returns "" (empty string) to skip remaining
    prompts, mimicking a user pressing Enter repeatedly.
    """
    it = iter(responses)

    def _input(prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            return ""

    return _input


def _skip_then(*finals: str) -> Any:
    """Create an input function that skips all prompts, then returns finals.

    Recognizes the "Write config.yaml?" and "Update workspace" prompts
    by their text, returning the next final response for each. All other
    prompts get "".
    """
    queue = list(finals)

    def _input(prompt: str) -> str:
        if ("Write config" in prompt or "Update workspace" in prompt) and queue:
            return queue.pop(0)
        return ""

    return _input


def _valid_config_dict() -> dict[str, Any]:
    """Return a valid config dict for testing."""
    from helpers import make_test_config_dict
    return make_test_config_dict()


# ---------------------------------------------------------------------------
# _edit_list
# ---------------------------------------------------------------------------


class TestEditList:
    def test_add_and_remove(self, capsys: pytest.CaptureFixture) -> None:
        inp = _make_input_fn(["new_status", "", "todo_hard", ""])
        result = _edit_list("statuses", ["todo_hard", "done"], input_fn=inp)
        assert "new_status" in result
        assert "todo_hard" not in result
        assert "done" in result

    def test_add_duplicate(self, capsys: pytest.CaptureFixture) -> None:
        inp = _make_input_fn(["done", "", ""])
        result = _edit_list("statuses", ["done"], input_fn=inp)
        assert result == ["done"]
        out = capsys.readouterr().out
        assert "already present" in out

    def test_remove_missing(self, capsys: pytest.CaptureFixture) -> None:
        inp = _make_input_fn(["", "nonexistent", ""])
        result = _edit_list("statuses", ["done"], input_fn=inp)
        assert result == ["done"]
        out = capsys.readouterr().out
        assert "not found" in out

    def test_eof_during_add(self) -> None:
        inp = _make_input_fn([])
        result = _edit_list("statuses", ["a"], input_fn=inp)
        assert result == ["a"]

    def test_eof_in_prompt(self) -> None:
        """_prompt handles EOFError and returns empty string."""
        from hypergumbo_tracker.configure import _prompt

        def raise_eof(msg: str) -> str:
            raise EOFError

        assert _prompt("test: ", input_fn=raise_eof) == ""

    def test_keyboard_interrupt_in_prompt(self) -> None:
        """_prompt handles KeyboardInterrupt and returns empty string."""
        from hypergumbo_tracker.configure import _prompt

        def raise_ki(msg: str) -> str:
            raise KeyboardInterrupt

        assert _prompt("test: ", input_fn=raise_ki) == ""


# ---------------------------------------------------------------------------
# Section configurators
# ---------------------------------------------------------------------------


class TestConfigureStatuses:
    def test_edit_statuses(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {"statuses": ["a", "b"]}
        inp = _make_input_fn(["c", "", "a", ""])
        _configure_statuses(config, input_fn=inp)
        assert config["statuses"] == ["b", "c"]


class TestConfigureKinds:
    def test_add_kind(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {
            "kinds": {"work_item": {"prefix": "WI", "description": "Work item"}},
        }
        # Skip allowed_statuses for work_item, add new kind "bug", remove nothing
        inp = _make_input_fn([
            "n",                 # add restriction to work_item? No
            "bug",               # add a new kind
            "BG",                # prefix
            "Bug report",        # description
            "",                  # done adding kinds
            "",                  # remove kind? no
        ])
        _configure_kinds(config, input_fn=inp)
        assert "bug" in config["kinds"]
        assert config["kinds"]["bug"]["prefix"] == "BG"

    def test_add_existing_kind(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {
            "kinds": {"work_item": {"prefix": "WI", "description": "Work item"}},
        }
        inp = _make_input_fn([
            "n",                 # add restriction? No
            "work_item",         # try to add existing
            "",                  # done adding
            "",                  # remove? no
        ])
        _configure_kinds(config, input_fn=inp)
        out = capsys.readouterr().out
        assert "already exists" in out

    def test_remove_kind(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {
            "kinds": {
                "work_item": {"prefix": "WI"},
                "bug": {"prefix": "BG"},
            },
        }
        inp = _make_input_fn([
            "n",             # add restriction to work_item? No
            "n",             # add restriction to bug? No
            "",              # done adding kinds
            "bug",           # remove bug
            "",              # done removing
        ])
        _configure_kinds(config, input_fn=inp)
        assert "bug" not in config["kinds"]

    def test_remove_nonexistent_kind(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {"kinds": {"work_item": {"prefix": "WI"}}}
        inp = _make_input_fn([
            "n",             # add restriction? No
            "",              # done adding
            "nonexistent",   # remove nonexistent
            "",              # done removing
        ])
        _configure_kinds(config, input_fn=inp)
        out = capsys.readouterr().out
        assert "not found" in out

    def test_edit_allowed_statuses(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {
            "kinds": {
                "invariant": {
                    "prefix": "INV",
                    "allowed_statuses": ["holding", "violated"],
                },
            },
        }
        inp = _make_input_fn([
            "deleted",        # add to allowed_statuses
            "",               # done adding
            "holding",        # remove from allowed_statuses
            "",               # done removing
            "",               # done adding kinds
            "",               # done removing kinds
        ])
        _configure_kinds(config, input_fn=inp)
        assert "deleted" in config["kinds"]["invariant"]["allowed_statuses"]
        assert "holding" not in config["kinds"]["invariant"]["allowed_statuses"]

    def test_add_restriction_to_unrestricted(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {
            "kinds": {"work_item": {"prefix": "WI"}},
        }
        inp = _make_input_fn([
            "y",             # add restriction? Yes
            "todo_hard",     # add status
            "",              # done adding
            "",              # done removing
            "",              # done adding kinds
            "",              # done removing kinds
        ])
        _configure_kinds(config, input_fn=inp)
        assert config["kinds"]["work_item"]["allowed_statuses"] == ["todo_hard"]

    def test_default_prefix(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {"kinds": {}}
        inp = _make_input_fn([
            "task",          # add new kind
            "",              # default prefix
            "",              # description
            "",              # done adding
            "",              # done removing
        ])
        _configure_kinds(config, input_fn=inp)
        assert config["kinds"]["task"]["prefix"] == "TAS"

    def test_non_dict_kinds_reset(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {"kinds": "invalid"}
        inp = _make_input_fn(["", ""])
        _configure_kinds(config, input_fn=inp)
        assert config["kinds"] == {}

    def test_non_dict_kind_spec_skipped(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {"kinds": {"bad": "not_a_dict", "good": {"prefix": "GD"}}}
        inp = _make_input_fn([
            "n",             # skip restriction for good
            "",              # done adding
            "",              # done removing
        ])
        _configure_kinds(config, input_fn=inp)
        # bad is still there but skipped
        assert "bad" in config["kinds"]


class TestConfigureStopHook:
    def test_edit_stop_hook(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {
            "stop_hook": {
                "blocking_statuses": ["todo_hard"],
                "resolved_statuses": ["done"],
                "human_only_statuses": [],
            },
        }
        inp = _make_input_fn([
            "",              # blocking: done adding
            "",              # blocking: done removing
            "",              # resolved: done adding
            "",              # resolved: done removing
            "",              # human_only: done adding
            "",              # human_only: done removing
            "workspace",     # change scope
        ])
        _configure_stop_hook(config, input_fn=inp)
        assert config["stop_hook"]["scope"] == "workspace"

    def test_keep_scope(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {
            "stop_hook": {
                "blocking_statuses": ["todo_hard"],
                "resolved_statuses": ["done"],
                "human_only_statuses": [],
                "scope": "all",
            },
        }
        inp = _make_input_fn([
            "", "", "", "", "", "",  # skip all list edits
            "invalid",              # invalid scope → ignored
        ])
        _configure_stop_hook(config, input_fn=inp)
        assert config["stop_hook"]["scope"] == "all"

    def test_non_dict_stop_hook(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {"stop_hook": "bad"}
        inp = _make_input_fn(["", "", "", "", "", "", ""])
        _configure_stop_hook(config, input_fn=inp)
        assert isinstance(config["stop_hook"], dict)


class TestConfigureTags:
    def test_edit_tags(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {"well_known_tags": ["a"]}
        inp = _make_input_fn(["b", "", "a", ""])
        _configure_tags(config, input_fn=inp)
        assert config["well_known_tags"] == ["b"]


class TestConfigureActorResolution:
    def test_edit_patterns(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {"actor_resolution": {"agent_usernames": ["*_agent"]}}
        inp = _make_input_fn(["bot_*", "", ""])
        _configure_actor_resolution(config, input_fn=inp)
        assert "bot_*" in config["actor_resolution"]["agent_usernames"]

    def test_non_dict_actor_resolution(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {"actor_resolution": "bad"}
        inp = _make_input_fn(["", ""])
        _configure_actor_resolution(config, input_fn=inp)
        assert isinstance(config["actor_resolution"], dict)


class TestConfigureLamportBranches:
    def test_edit_branches(self, capsys: pytest.CaptureFixture) -> None:
        config: dict[str, Any] = {"lamport_branches": ["dev"]}
        inp = _make_input_fn(["staging", "", ""])
        _configure_lamport_branches(config, input_fn=inp)
        assert "staging" in config["lamport_branches"]


# ---------------------------------------------------------------------------
# _compute_diff
# ---------------------------------------------------------------------------


class TestComputeDiff:
    def test_no_changes(self) -> None:
        d = {"a": 1}
        assert _compute_diff(d, d) == ["(no changes)"]

    def test_with_changes(self) -> None:
        old = {"a": 1}
        new = {"a": 2}
        diff = _compute_diff(old, new)
        assert any("-" in line for line in diff)
        assert any("+" in line for line in diff)


# ---------------------------------------------------------------------------
# run_configure — full flow
# ---------------------------------------------------------------------------


class TestRunConfigure:
    def test_agent_rejected(self, tmp_path: Path, mock_agent_uid: None) -> None:
        root = _make_agent_dir(tmp_path)
        result = run_configure(root)
        assert result == 1

    def test_agent_rejected_with_config(self, tmp_path: Path, mock_agent_uid: None) -> None:
        root = _make_agent_dir(tmp_path)
        _write_config(root)
        result = run_configure(root)
        assert result == 1

    def test_full_flow_no_changes_abort(self, tmp_path: Path,
                                        mock_human_uid: None,
                                        capsys: pytest.CaptureFixture) -> None:
        root = _make_agent_dir(tmp_path)
        config_path = _write_config(root)

        # Skip everything, then abort write
        inp = _skip_then("n")
        result = run_configure(root, input_fn=inp)
        assert result == 0
        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_full_flow_write(self, tmp_path: Path,
                              mock_human_uid: None,
                              capsys: pytest.CaptureFixture) -> None:
        root = _make_agent_dir(tmp_path)
        config_path = _write_config(root)

        # Skip everything, then confirm write
        inp = _skip_then("y")
        result = run_configure(root, input_fn=inp)
        assert result == 0
        out = capsys.readouterr().out
        assert "Wrote" in out

        # Verify file is 0444
        mode = config_path.stat().st_mode & 0o777
        assert mode == 0o444

    def test_write_with_workspace_config(self, tmp_path: Path,
                                          mock_human_uid: None,
                                          capsys: pytest.CaptureFixture) -> None:
        root = _make_agent_dir(tmp_path)
        _write_config(root)
        ws_config = root / "tracker-workspace" / "config.yaml"
        ws_config.write_text(yaml.dump({"placeholder": True}))

        # Skip everything, confirm main write, confirm workspace write
        inp = _skip_then("y", "y")
        result = run_configure(root, input_fn=inp)
        assert result == 0
        out = capsys.readouterr().out
        assert "tracker-workspace" in out

        # Verify workspace config is 0444
        mode = ws_config.stat().st_mode & 0o777
        assert mode == 0o444

    def test_write_decline_workspace(self, tmp_path: Path,
                                      mock_human_uid: None,
                                      capsys: pytest.CaptureFixture) -> None:
        root = _make_agent_dir(tmp_path)
        _write_config(root)
        ws_config = root / "tracker-workspace" / "config.yaml"
        ws_config.write_text(yaml.dump({"placeholder": True}))

        # Skip everything, confirm main write, decline workspace
        inp = _skip_then("y", "n")
        result = run_configure(root, input_fn=inp)
        assert result == 0

    def test_validation_failure(self, tmp_path: Path,
                                 mock_human_uid: None,
                                 capsys: pytest.CaptureFixture) -> None:
        root = _make_agent_dir(tmp_path)
        # Write config with empty statuses so validation will fail after editing
        config_data = _valid_config_dict()
        _write_config(root, config_data)

        # Remove all statuses to trigger validation error
        responses: list[str] = []
        # Statuses: remove all
        for s in config_data["statuses"]:
            responses.extend(["", s])  # add nothing, remove each status
        responses.append("")  # done removing
        # Skip everything else
        responses.extend([""] * 30)

        inp = _make_input_fn(responses)
        result = run_configure(root, input_fn=inp)
        assert result == 1
        err = capsys.readouterr().err
        assert "invalid configuration" in err

    def test_no_config_uses_template(self, tmp_path: Path,
                                      mock_human_uid: None,
                                      capsys: pytest.CaptureFixture) -> None:
        root = _make_agent_dir(tmp_path)
        # Write template but no config
        from helpers import make_test_config_dict
        template_data = make_test_config_dict()
        template_path = root / "tracker" / "config.yaml.template"
        template_path.write_text(yaml.dump(template_data))

        # Skip everything, write
        inp = _skip_then("y")
        result = run_configure(root, input_fn=inp)
        assert result == 0

        # config.yaml should now exist
        config_path = root / "tracker" / "config.yaml"
        assert config_path.exists()

    def test_no_config_no_template(self, tmp_path: Path,
                                    mock_human_uid: None,
                                    capsys: pytest.CaptureFixture) -> None:
        root = _make_agent_dir(tmp_path)
        # No config, no template — starts from empty dict, validation will fail
        inp = _skip_then("y")
        result = run_configure(root, input_fn=inp)
        assert result == 1

    def test_corrupt_config_falls_back_to_template(
        self, tmp_path: Path, mock_human_uid: None,
        capsys: pytest.CaptureFixture,
    ) -> None:
        root = _make_agent_dir(tmp_path)
        config_path = root / "tracker" / "config.yaml"
        config_path.write_text("not: [valid: yaml: {{{")

        from helpers import make_test_config_dict
        template_path = root / "tracker" / "config.yaml.template"
        template_path.write_text(yaml.dump(make_test_config_dict()))

        inp = _skip_then("y")
        result = run_configure(root, input_fn=inp)
        assert result == 0
        err = capsys.readouterr().err
        assert "unparseable" in err

    def test_locked_config_is_unlocked_for_read(
        self, tmp_path: Path, mock_human_uid: None,
        capsys: pytest.CaptureFixture,
    ) -> None:
        root = _make_agent_dir(tmp_path)
        config_path = _write_config(root)
        config_lock(config_path)
        assert config_path.stat().st_mode & 0o777 == 0o444

        inp = _skip_then("y")
        result = run_configure(root, input_fn=inp)
        assert result == 0

        # Should be locked after write
        assert config_path.stat().st_mode & 0o777 == 0o444

    def test_agent_rejected_with_custom_patterns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        """Agent with custom pattern in config is still rejected."""
        root = _make_agent_dir(tmp_path)
        from helpers import make_test_config_dict
        config_data = make_test_config_dict()
        config_path = _write_config(root, config_data)

        # Mock the current user as matching the agent pattern
        with patch("hypergumbo_tracker.configure.resolve_actor",
                    return_value=("agent", "test_agent")):
            result = run_configure(root)
        assert result == 1

    def test_config_yaml_template_unparseable(
        self, tmp_path: Path, mock_human_uid: None,
        capsys: pytest.CaptureFixture,
    ) -> None:
        root = _make_agent_dir(tmp_path)
        template_path = root / "tracker" / "config.yaml.template"
        template_path.write_text("not: [valid: yaml: {{{")

        # No config.yaml, template is broken → starts from empty
        inp = _skip_then("y")
        result = run_configure(root, input_fn=inp)
        # Validation will fail with empty config
        assert result == 1


# ---------------------------------------------------------------------------
# CLI integration (htrac setup configure)
# ---------------------------------------------------------------------------


class TestSetupConfigureCLI:
    def test_setup_configure_dispatches(
        self, tmp_path: Path, mock_agent_uid: None,
    ) -> None:
        """htrac setup configure dispatches to run_configure (agent → error)."""
        from hypergumbo_tracker.cli import main

        root = _make_agent_dir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "setup", "configure"])
        # Agent is rejected → exit 1
        assert exc.value.code == 1

    def test_setup_configure_human(
        self, tmp_path: Path, mock_human_uid: None,
        capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """htrac setup configure with human runs the full configure flow."""
        from hypergumbo_tracker.cli import main

        root = _make_agent_dir(tmp_path)
        _write_config(root)

        # Mock input to skip everything and abort
        responses = [""] * 50 + ["n"]
        inp_iter = iter(responses)
        monkeypatch.setattr("builtins.input", lambda prompt: next(inp_iter, ""))

        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "setup", "configure"])
        assert exc.value.code == 0

    def test_setup_configure_auto_discover_root(
        self, tmp_path: Path, mock_human_uid: None,
        capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup configure auto-discovers .agent/ when no --root given."""
        from hypergumbo_tracker.cli import main

        root = _make_agent_dir(tmp_path)
        _write_config(root)
        monkeypatch.chdir(tmp_path)

        responses = [""] * 50 + ["n"]
        inp_iter = iter(responses)
        monkeypatch.setattr("builtins.input", lambda prompt: next(inp_iter, ""))

        with pytest.raises(SystemExit) as exc:
            main(["setup", "configure"])
        assert exc.value.code == 0

    def test_setup_configure_with_root_arg(
        self, tmp_path: Path, mock_human_uid: None,
        capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup configure respects --root argument."""
        from hypergumbo_tracker.cli import main

        root = _make_agent_dir(tmp_path)
        _write_config(root)

        responses = [""] * 50 + ["n"]
        inp_iter = iter(responses)
        monkeypatch.setattr("builtins.input", lambda prompt: next(inp_iter, ""))

        with pytest.raises(SystemExit) as exc:
            main(["setup", "--root", str(root), "configure"])
        assert exc.value.code == 0

    def test_setup_configure_no_agent_dir_fallback(
        self, tmp_path: Path, mock_human_uid: None,
        capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """setup configure without .agent/ falls back to cwd/.agent."""
        from hypergumbo_tracker.cli import main

        # Use a directory with no .agent/ subdirectory
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        monkeypatch.chdir(empty_dir)

        # Monkeypatch input to avoid stdin read errors
        monkeypatch.setattr("builtins.input", lambda prompt: "")

        # run_configure will try to load config from cwd/.agent which doesn't exist
        # It will still run but validation will fail since no config
        with pytest.raises(SystemExit) as exc:
            main(["setup", "configure"])
        # Should exit with error (validation failure on empty config)
        assert exc.value.code == 1

    def test_setup_without_configure_still_works(
        self, tmp_path: Path, mock_human_uid: None,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """htrac setup (without configure) still runs the wizard."""
        from hypergumbo_tracker.cli import main

        root = _make_agent_dir(tmp_path)
        # setup without 'configure' should run the normal wizard
        with pytest.raises(SystemExit) as exc:
            main(["--tracker-root", str(root), "setup", "--root", str(root)])
        # Should succeed (setup wizard runs checks)
        assert exc.value.code in (0, 1)  # May warn but should not crash
