# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for :mod:`hypergumbo_lang_rust_analyzer.gate` (WI-duzul Slice C gate)."""
from __future__ import annotations

import pytest

from hypergumbo_lang_rust_analyzer.gate import (
    ENV_VAR_NAME,
    _is_env_enabled,
    _is_flag_enabled,
    should_use_rust_analyzer_backend,
)


class TestIsEnvEnabled:
    @pytest.mark.parametrize(
        "value",
        ["1", "true", "True", "TRUE", "yes", "YES", "on", "On",
         " true ", "true\n"],
    )
    def test_truthy(self, value: str) -> None:
        assert _is_env_enabled({ENV_VAR_NAME: value}) is True

    @pytest.mark.parametrize(
        "value", ["", "0", "false", "no", "off", "maybe", " 0 "],
    )
    def test_falsy(self, value: str) -> None:
        assert _is_env_enabled({ENV_VAR_NAME: value}) is False

    def test_missing_key(self) -> None:
        assert _is_env_enabled({}) is False


class TestIsFlagEnabled:
    @pytest.mark.parametrize(
        "value",
        ["rust-analyzer", "RUST-ANALYZER", "rust_analyzer", "scip",
         " rust-analyzer "],
    )
    def test_selects_scip(self, value: str) -> None:
        assert _is_flag_enabled(value) is True

    @pytest.mark.parametrize(
        "value", [None, "", "tree-sitter", "default", "rust.py", "other"],
    )
    def test_does_not_select(self, value) -> None:
        assert _is_flag_enabled(value) is False


class TestShouldUseBackend:
    def test_opt_in_via_env_and_available(self) -> None:
        assert should_use_rust_analyzer_backend(
            environ={ENV_VAR_NAME: "1"},
            is_available=lambda: True,
        ) is True

    def test_opt_in_via_flag_and_available(self) -> None:
        assert should_use_rust_analyzer_backend(
            backend_flag="rust-analyzer",
            environ={},
            is_available=lambda: True,
        ) is True

    def test_opt_in_honoured_but_binary_missing_returns_false(self) -> None:
        """User asked but binary missing → False (caller falls through)."""
        assert should_use_rust_analyzer_backend(
            environ={ENV_VAR_NAME: "1"},
            is_available=lambda: False,
        ) is False

    def test_no_opt_in_short_circuits_without_calling_is_available(
        self,
    ) -> None:
        """When neither env nor flag opts in, is_available is never invoked."""
        calls: list[int] = []

        def _is_available() -> bool:  # pragma: no cover — should not fire
            calls.append(1)
            return True

        assert should_use_rust_analyzer_backend(
            environ={}, is_available=_is_available,
        ) is False
        assert calls == []

    def test_flag_beats_missing_env(self) -> None:
        assert should_use_rust_analyzer_backend(
            backend_flag="scip", environ={},
            is_available=lambda: True,
        ) is True

    def test_env_beats_missing_flag(self) -> None:
        assert should_use_rust_analyzer_backend(
            backend_flag=None, environ={ENV_VAR_NAME: "yes"},
            is_available=lambda: True,
        ) is True

    def test_neither_returns_false(self) -> None:
        assert should_use_rust_analyzer_backend(
            environ={}, is_available=lambda: True,
        ) is False

    def test_default_environ_is_os_environ(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With no environ kwarg, the gate reads the real os.environ."""
        monkeypatch.setenv(ENV_VAR_NAME, "1")
        assert should_use_rust_analyzer_backend(
            is_available=lambda: True,
        ) is True
        monkeypatch.delenv(ENV_VAR_NAME, raising=False)
        assert should_use_rust_analyzer_backend(
            is_available=lambda: True,
        ) is False

    def test_default_is_available_resolver_wired_in(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With no is_available kwarg, the gate calls the real resolver.

        We monkeypatch the resolver's downstream shutil.which so no real
        binary detection fires, then assert the gate chain still reaches
        that resolver when neither is_available nor environ is injected.
        """
        monkeypatch.setenv(ENV_VAR_NAME, "1")
        monkeypatch.setattr("shutil.which", lambda _name: None)
        assert should_use_rust_analyzer_backend() is False
