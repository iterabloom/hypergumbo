# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the install-extras / uninstall-extras CLI umbrella (WI-huham)."""
from __future__ import annotations

import subprocess
from argparse import Namespace
from dataclasses import dataclass
from unittest.mock import patch

import pytest


@dataclass
class _Completed:
    returncode: int = 0
    stderr: bytes = b""
    stdout: bytes = b""


# ---------------------------------------------------------------------------
# cmd_install_extras --check
# ---------------------------------------------------------------------------


class TestInstallExtrasCheck:
    def test_all_present_returns_zero(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_install_extras

        args = Namespace(check=True, skip=None, quiet=False)
        components = [
            ("gitleaks", lambda: True, None, None),
            ("embeddings", lambda: True, None, None),
            ("rust-analyzer", lambda: True, None, None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_install_extras(args)
        assert rc == 0
        out = capsys.readouterr().out
        for name in ("gitleaks", "embeddings", "rust-analyzer"):
            assert name in out
            assert "\u2713" in out  # check mark for installed

    def test_one_missing_returns_one(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_install_extras

        args = Namespace(check=True, skip=None, quiet=False)
        components = [
            ("gitleaks", lambda: True, None, None),
            ("embeddings", lambda: False, None, None),
            ("rust-analyzer", lambda: True, None, None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_install_extras(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "embeddings:" in out
        assert "\u2717" in out  # cross mark for missing

    def test_skip_excludes_from_table(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_install_extras

        args = Namespace(check=True, skip="gitleaks, embeddings", quiet=False)
        components = [
            ("gitleaks", lambda: False, None, None),
            ("embeddings", lambda: False, None, None),
            ("rust-analyzer", lambda: True, None, None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_install_extras(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "gitleaks" not in out
        assert "embeddings" not in out
        assert "rust-analyzer" in out

    def test_skip_accepts_empty_segments(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--skip 'gitleaks,,' should not produce an empty-string entry."""
        from hypergumbo_core.cli import cmd_install_extras

        args = Namespace(check=True, skip="gitleaks,,", quiet=False)
        components = [
            ("gitleaks", lambda: True, None, None),
            ("embeddings", lambda: True, None, None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_install_extras(args)
        assert rc == 0


# ---------------------------------------------------------------------------
# cmd_install_extras (install path)
# ---------------------------------------------------------------------------


class TestInstallExtrasInstall:
    def test_skips_already_installed_components(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_install_extras

        called: list[str] = []

        def _install_never(quiet: bool = False) -> bool:  # pragma: no cover
            called.append("never")
            return True

        args = Namespace(check=False, skip=None, quiet=False)
        components = [
            ("gitleaks", lambda: True, _install_never, lambda quiet=False: True),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_install_extras(args)
        assert rc == 0
        assert called == []
        assert "already installed" in capsys.readouterr().out

    def test_runs_all_missing_installers(self) -> None:
        from hypergumbo_core.cli import cmd_install_extras

        called: list[str] = []

        def _make_install(name: str):
            def _install(quiet: bool = False) -> bool:
                called.append(name)
                return True
            return _install

        args = Namespace(check=False, skip=None, quiet=True)
        components = [
            ("gitleaks", lambda: False, _make_install("gitleaks"), None),
            ("embeddings", lambda: False, _make_install("embeddings"), None),
            ("rust-analyzer", lambda: False, _make_install("rust-analyzer"), None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_install_extras(args)
        assert rc == 0
        assert called == ["gitleaks", "embeddings", "rust-analyzer"]

    def test_install_failure_returns_one_but_continues(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_install_extras

        called: list[str] = []

        def _make_install(name: str, ok: bool):
            def _install(quiet: bool = False) -> bool:
                called.append(name)
                return ok
            return _install

        args = Namespace(check=False, skip=None, quiet=True)
        components = [
            ("gitleaks", lambda: False, _make_install("gitleaks", False), None),
            ("embeddings", lambda: False, _make_install("embeddings", True), None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_install_extras(args)
        assert rc == 1
        # embeddings still ran despite gitleaks failing.
        assert called == ["gitleaks", "embeddings"]
        assert "install failed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_uninstall_extras
# ---------------------------------------------------------------------------


class TestUninstallExtras:
    def test_all_succeed_returns_zero(self) -> None:
        from hypergumbo_core.cli import cmd_uninstall_extras

        called: list[str] = []

        def _make_uninstall(name: str, ok: bool):
            def _uninstall(quiet: bool = False) -> bool:
                called.append(name)
                return ok
            return _uninstall

        args = Namespace(skip=None, quiet=True)
        components = [
            ("gitleaks", lambda: True, None, _make_uninstall("gitleaks", True)),
            ("embeddings", lambda: True, None, _make_uninstall("embeddings", True)),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_uninstall_extras(args)
        assert rc == 0
        assert called == ["gitleaks", "embeddings"]

    def test_any_failure_returns_one(self) -> None:
        from hypergumbo_core.cli import cmd_uninstall_extras

        args = Namespace(skip=None, quiet=True)
        components = [
            ("gitleaks", lambda: True, None, lambda quiet=False: False),
            ("embeddings", lambda: True, None, lambda quiet=False: True),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_uninstall_extras(args)
        assert rc == 1

    def test_skip_excludes_components(self) -> None:
        from hypergumbo_core.cli import cmd_uninstall_extras

        called: list[str] = []

        def _make_uninstall(name: str):
            def _uninstall(quiet: bool = False) -> bool:
                called.append(name)
                return True
            return _uninstall

        args = Namespace(skip="gitleaks", quiet=True)
        components = [
            ("gitleaks", lambda: True, None, _make_uninstall("gitleaks")),
            ("embeddings", lambda: True, None, _make_uninstall("embeddings")),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_uninstall_extras(args)
        assert rc == 0
        assert called == ["embeddings"]


# ---------------------------------------------------------------------------
# _extras_components sanity + embeddings adapter coverage
# ---------------------------------------------------------------------------


class TestExtrasComponentsTable:
    def test_returns_three_rows_with_expected_names(self) -> None:
        from hypergumbo_core.cli import _extras_components

        rows = _extras_components()
        names = [row[0] for row in rows]
        assert names == ["gitleaks", "embeddings", "rust-analyzer"]
        for name, is_available, install, uninstall in rows:
            assert callable(is_available), name
            assert callable(install), name
            assert callable(uninstall), name


class TestEmbeddingsAdapters:
    """The embeddings install/uninstall adapters shell out to pip; these
    tests inject a runner via :mod:`subprocess` patching so no real pip
    invocation fires."""

    def test_install_success(self) -> None:
        from hypergumbo_core.cli import _install_embeddings_impl

        with patch("subprocess.run", return_value=_Completed()):
            assert _install_embeddings_impl(quiet=True) is True

    def test_install_success_emits_chatter(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _install_embeddings_impl

        with patch("subprocess.run", return_value=_Completed()):
            assert _install_embeddings_impl() is True
        out = capsys.readouterr().out
        assert "Installing embedding dependencies" in out
        assert "Done" in out

    def test_install_nonzero_exit(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _install_embeddings_impl

        with patch(
            "subprocess.run",
            return_value=_Completed(returncode=1, stderr=b"pip error"),
        ):
            assert _install_embeddings_impl(quiet=True) is False
        err = capsys.readouterr().err
        assert "pip install" in err
        assert "pip error" in err

    def test_install_timeout(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _install_embeddings_impl

        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="pip", timeout=1)

        with patch("subprocess.run", side_effect=_raise):
            assert _install_embeddings_impl(quiet=True) is False
        assert "Error installing embeddings" in capsys.readouterr().err

    def test_install_nonzero_exit_str_stderr(self) -> None:
        from hypergumbo_core.cli import _install_embeddings_impl

        with patch(
            "subprocess.run",
            return_value=_Completed(
                returncode=1, stderr="already-str",  # type: ignore[arg-type]
            ),
        ):
            assert _install_embeddings_impl(quiet=True) is False

    def test_uninstall_success(self) -> None:
        from hypergumbo_core.cli import _uninstall_embeddings_impl

        with patch("subprocess.run", return_value=_Completed()):
            assert _uninstall_embeddings_impl(quiet=True) is True

    def test_uninstall_success_emits_chatter(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _uninstall_embeddings_impl

        with patch("subprocess.run", return_value=_Completed()):
            assert _uninstall_embeddings_impl() is True
        out = capsys.readouterr().out
        assert "Removing embedding dependencies" in out
        assert "Done" in out

    def test_uninstall_timeout(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _uninstall_embeddings_impl

        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="pip", timeout=1)

        with patch("subprocess.run", side_effect=_raise):
            assert _uninstall_embeddings_impl(quiet=True) is False
        assert "Error uninstalling embeddings" in capsys.readouterr().err
