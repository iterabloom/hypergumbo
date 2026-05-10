# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for :mod:`hypergumbo_core.rust_analyzer_install` (WI-dotud)."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

import pytest

from hypergumbo_core.rust_analyzer_install import (
    install_rust_analyzer,
    is_rust_analyzer_available,
    uninstall_rust_analyzer,
)


@dataclass
class _Completed:
    returncode: int = 0
    stderr: bytes = b""
    stdout: bytes = b""


def _which_missing(_name: str) -> Optional[str]:
    return None


def _which_found(name: str) -> str:
    return f"/fake/bin/{name}"


# ---------------------------------------------------------------------------
# is_rust_analyzer_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_resolves_when_binary_on_path(self) -> None:
        assert is_rust_analyzer_available(which=_which_found) is True

    def test_returns_false_when_missing(self) -> None:
        assert is_rust_analyzer_available(which=_which_missing) is False

    def test_default_which_is_shutil(self) -> None:
        """Without an override, the resolver is shutil.which.

        The assertion tolerates either outcome — the test env may or
        may not have rust-analyzer installed; the contract is only that
        a bool is returned and no exception is raised.
        """
        result = is_rust_analyzer_available()
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# install_rust_analyzer
# ---------------------------------------------------------------------------


class TestInstall:
    def test_missing_rustup_fails_without_invocation(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _runner(*_a, **_kw):  # pragma: no cover — should not fire
            raise AssertionError("runner called when rustup missing")

        ok = install_rust_analyzer(which=_which_missing, runner=_runner)
        assert ok is False
        err = capsys.readouterr().err
        assert "rustup is not on PATH" in err

    def test_successful_install(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _runner(cmd, *, capture_output, timeout):
            assert cmd == [
                "/fake/bin/rustup", "component", "add", "rust-analyzer",
            ]
            assert capture_output is True
            assert timeout == 300.0
            return _Completed()

        ok = install_rust_analyzer(which=_which_found, runner=_runner)
        assert ok is True
        out = capsys.readouterr().out
        assert "Installing rust-analyzer via rustup" in out
        assert "Done" in out

    def test_quiet_mode_suppresses_chatter(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        ok = install_rust_analyzer(
            quiet=True,
            which=_which_found,
            runner=lambda *a, **kw: _Completed(),
        )
        assert ok is True
        out = capsys.readouterr().out
        assert out == ""

    def test_nonzero_exit_reports_stderr(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _runner(cmd, *, capture_output, timeout):
            return _Completed(returncode=1, stderr=b"toolchain missing")

        ok = install_rust_analyzer(which=_which_found, runner=_runner)
        assert ok is False
        err = capsys.readouterr().err
        assert "exited 1" in err
        assert "toolchain missing" in err

    def test_timeout_reports_cleanly(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _runner(cmd, *, capture_output, timeout):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        ok = install_rust_analyzer(which=_which_found, runner=_runner)
        assert ok is False
        err = capsys.readouterr().err
        assert "timed out" in err

    def test_oserror_reports_cleanly(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _runner(cmd, *, capture_output, timeout):
            raise OSError("permission denied")

        ok = install_rust_analyzer(which=_which_found, runner=_runner)
        assert ok is False
        err = capsys.readouterr().err
        assert "Error invoking rustup" in err
        assert "permission denied" in err

    def test_stderr_str_also_handled(self) -> None:
        """Some runners return stderr as str (CompletedProcess text=True)."""
        def _runner(cmd, *, capture_output, timeout):
            return _Completed(
                returncode=1, stderr="already-str",  # type: ignore[arg-type]
            )

        ok = install_rust_analyzer(
            quiet=True, which=_which_found, runner=_runner,
        )
        assert ok is False


# ---------------------------------------------------------------------------
# uninstall_rust_analyzer
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_missing_rustup_is_success_noop(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        ok = uninstall_rust_analyzer(which=_which_missing)
        assert ok is True
        out = capsys.readouterr().out
        assert "rustup not on PATH" in out

    def test_missing_rustup_quiet_mode(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        ok = uninstall_rust_analyzer(quiet=True, which=_which_missing)
        assert ok is True
        assert capsys.readouterr().out == ""

    def test_successful_uninstall(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _runner(cmd, *, capture_output, timeout):
            assert cmd == [
                "/fake/bin/rustup", "component", "remove", "rust-analyzer",
            ]
            assert timeout == 120.0
            return _Completed()

        ok = uninstall_rust_analyzer(which=_which_found, runner=_runner)
        assert ok is True
        out = capsys.readouterr().out
        assert "Removing rust-analyzer via rustup" in out
        assert "Done" in out

    def test_nonzero_exit_reports_stderr(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _runner(cmd, *, capture_output, timeout):
            return _Completed(returncode=2, stderr=b"not installed")

        ok = uninstall_rust_analyzer(which=_which_found, runner=_runner)
        assert ok is False
        err = capsys.readouterr().err
        assert "exited 2" in err
        assert "not installed" in err

    def test_timeout_reports_cleanly(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _runner(cmd, *, capture_output, timeout):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        ok = uninstall_rust_analyzer(which=_which_found, runner=_runner)
        assert ok is False
        err = capsys.readouterr().err
        assert "timed out" in err

    def test_oserror_reports_cleanly(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        def _runner(cmd, *, capture_output, timeout):
            raise OSError("disk full")

        ok = uninstall_rust_analyzer(which=_which_found, runner=_runner)
        assert ok is False
        err = capsys.readouterr().err
        assert "Error invoking rustup" in err
        assert "disk full" in err

    def test_stderr_str_also_handled(self) -> None:
        def _runner(cmd, *, capture_output, timeout):
            return _Completed(
                returncode=1, stderr="already-str",  # type: ignore[arg-type]
            )

        ok = uninstall_rust_analyzer(
            quiet=True, which=_which_found, runner=_runner,
        )
        assert ok is False

    def test_quiet_successful_uninstall(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        ok = uninstall_rust_analyzer(
            quiet=True,
            which=_which_found,
            runner=lambda *a, **kw: _Completed(),
        )
        assert ok is True
        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# CLI command wrappers
# ---------------------------------------------------------------------------


class TestCmdInstallRustAnalyzer:
    def test_check_when_available_returns_zero(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from hypergumbo_core.cli import cmd_install_rust_analyzer

        args = Namespace(check=True, quiet=False)
        # Also mock the BUG-06 integration check — without this, test-core's
        # isolated CI job (no hypergumbo-lang-rust-analyzer installed) would
        # report rc==1 because the integration row reports "not installed".
        with patch(
            "hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available",
            return_value=True,
        ), patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=True,
        ):
            rc = cmd_install_rust_analyzer(args)
        assert rc == 0
        assert "installed" in capsys.readouterr().out

    def test_check_when_unavailable_returns_one(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from hypergumbo_core.cli import cmd_install_rust_analyzer

        args = Namespace(check=True, quiet=False)
        with patch(
            "hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available",
            return_value=False,
        ):
            rc = cmd_install_rust_analyzer(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "not installed" in out
        assert "hypergumbo install-rust-analyzer" in out

    def test_install_success_returns_zero(self) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from hypergumbo_core.cli import cmd_install_rust_analyzer

        args = Namespace(check=False, quiet=True)
        # Without the integration mock, the BUG-06 gate refuses to install
        # the binary alone (rc=2) — exercise the post-gate success path.
        with patch(
            "hypergumbo_core.rust_analyzer_install.install_rust_analyzer",
            return_value=True,
        ), patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=True,
        ):
            rc = cmd_install_rust_analyzer(args)
        assert rc == 0

    def test_install_failure_returns_one(self) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from hypergumbo_core.cli import cmd_install_rust_analyzer

        args = Namespace(check=False, quiet=True)
        with patch(
            "hypergumbo_core.rust_analyzer_install.install_rust_analyzer",
            return_value=False,
        ), patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=True,
        ):
            rc = cmd_install_rust_analyzer(args)
        assert rc == 1

    # WI-jinoh / BUG-06: install-rust-analyzer must surface the
    # integration-package-missing case rather than installing the rustup
    # binary into a SCIP-backend-can-never-engage state.

    def test_check_reports_integration_status_when_present(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from hypergumbo_core.cli import cmd_install_rust_analyzer

        args = Namespace(check=True, quiet=False)
        with patch(
            "hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available",
            return_value=True,
        ), patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=True,
        ):
            rc = cmd_install_rust_analyzer(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "rust-analyzer:" in out
        assert "hypergumbo-lang-rust-analyzer:" in out
        # When both are present, no troubleshooting hint should appear.
        assert "BUG-06" not in out

    def test_check_returns_one_and_flags_bug06_when_integration_missing(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from hypergumbo_core.cli import cmd_install_rust_analyzer

        args = Namespace(check=True, quiet=False)
        with patch(
            "hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available",
            return_value=True,
        ), patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=False,
        ):
            rc = cmd_install_rust_analyzer(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "hypergumbo-lang-rust-analyzer:" in out
        assert "not installed" in out
        assert "BUG-06" in out

    def test_install_errors_when_integration_missing(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from hypergumbo_core.cli import cmd_install_rust_analyzer

        args = Namespace(check=False, quiet=True)
        with patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=False,
        ), patch(
            "hypergumbo_core.rust_analyzer_install.install_rust_analyzer",
        ) as mock_install:
            rc = cmd_install_rust_analyzer(args)
        # Refuse to install the binary alone — it would leave the SCIP
        # backend a silent no-op. install_rust_analyzer must NOT be called.
        assert rc == 2
        mock_install.assert_not_called()
        err = capsys.readouterr().err
        assert "hypergumbo-lang-rust-analyzer" in err
        assert "BUG-06" in err


class TestIsRustAnalyzerIntegrationInstalled:
    """WI-jinoh / BUG-06: helper that distinguishes the rustup-binary
    availability check from the Python-wrapper-importability check.
    """

    def test_returns_true_when_package_importable(self) -> None:
        from importlib.machinery import ModuleSpec
        from unittest.mock import patch

        from hypergumbo_core.rust_analyzer_install import (
            is_rust_analyzer_integration_installed,
        )

        # Mock find_spec to return a non-None ModuleSpec, simulating an
        # importable package without depending on the optional integration
        # package being installed in the test environment (test-core's
        # isolated CI job has hypergumbo-core only).
        fake_spec = ModuleSpec("hypergumbo_lang_rust_analyzer", loader=None)
        with patch("importlib.util.find_spec", return_value=fake_spec):
            assert is_rust_analyzer_integration_installed() is True

    def test_returns_false_when_package_missing(self) -> None:
        from unittest.mock import patch

        from hypergumbo_core.rust_analyzer_install import (
            is_rust_analyzer_integration_installed,
        )

        with patch("importlib.util.find_spec", return_value=None):
            assert is_rust_analyzer_integration_installed() is False


class TestCmdUninstallRustAnalyzer:
    def test_uninstall_success_returns_zero(self) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from hypergumbo_core.cli import cmd_uninstall_rust_analyzer

        args = Namespace(quiet=True)
        with patch(
            "hypergumbo_core.rust_analyzer_install.uninstall_rust_analyzer",
            return_value=True,
        ):
            rc = cmd_uninstall_rust_analyzer(args)
        assert rc == 0

    def test_uninstall_failure_returns_one(self) -> None:
        from argparse import Namespace
        from unittest.mock import patch

        from hypergumbo_core.cli import cmd_uninstall_rust_analyzer

        args = Namespace(quiet=True)
        with patch(
            "hypergumbo_core.rust_analyzer_install.uninstall_rust_analyzer",
            return_value=False,
        ):
            rc = cmd_uninstall_rust_analyzer(args)
        assert rc == 1
