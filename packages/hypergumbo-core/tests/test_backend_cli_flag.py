# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the --backend CLI flag (WI-vozof).

Validates that `hypergumbo --backend rust-analyzer <subcommand> ...`
translates the flag into the `HYPERGUMBO_RUST_ANALYZER=1` environment
variable the gate reads, matching the --debug-stripping convention so
the flag works in any position.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from hypergumbo_core.cli import build_parser, main


ENV_VAR = "HYPERGUMBO_RUST_ANALYZER"


@pytest.fixture(autouse=True)
def _scrub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with no pre-existing opt-in env var."""
    monkeypatch.delenv(ENV_VAR, raising=False)


class TestParser:
    def test_backend_tree_sitter_accepted(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--backend", "tree-sitter", "sketch", "."])
        assert args.backend == "tree-sitter"

    def test_backend_rust_analyzer_accepted(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--backend", "rust-analyzer", "sketch", "."])
        assert args.backend == "rust-analyzer"

    def test_backend_default_is_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["sketch", "."])
        assert args.backend is None

    def test_backend_rejects_unknown_choice(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--backend", "bogus", "sketch", "."])
        err = capsys.readouterr().err
        assert "invalid choice" in err
        assert "bogus" in err


class TestMainStripping:
    def test_backend_rust_analyzer_sets_env(
        self, tmp_path,
    ) -> None:
        """--backend rust-analyzer exports HYPERGUMBO_RUST_ANALYZER=1."""
        assert ENV_VAR not in os.environ

        # Use a command that bails quickly (cache-status on an empty cache).
        # Mock both v5.0.0 / WI-jinoh parse-time gates so test-core's
        # isolated CI job (no integration package, no rust-analyzer binary
        # functional) doesn't trip sys.exit(2). The first gate covers
        # BUG-06; the second covers the broken-rustup-proxy class fixed
        # in the post-v5.0.0 smoke-test patch.
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0), patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=True,
        ), patch(
            "hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available",
            return_value=True,
        ):
            rc = main([
                "--backend", "rust-analyzer", "cache-status", "--quiet",
            ])
        assert rc == 0
        assert os.environ.get(ENV_VAR) == "1"

    def test_backend_tree_sitter_does_not_set_env(self) -> None:
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
            rc = main(["--backend", "tree-sitter", "cache-status", "--quiet"])
        assert rc == 0
        assert ENV_VAR not in os.environ

    def test_backend_flag_in_post_subcommand_position(self) -> None:
        """`hypergumbo cache-status --backend rust-analyzer --quiet` works too."""
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0), patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=True,
        ), patch(
            "hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available",
            return_value=True,
        ):
            rc = main([
                "cache-status", "--backend", "rust-analyzer", "--quiet",
            ])
        assert rc == 0
        assert os.environ.get(ENV_VAR) == "1"

    def test_backend_equals_form_supported(self) -> None:
        """argparse-style --backend=rust-analyzer is also stripped."""
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0), patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=True,
        ), patch(
            "hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available",
            return_value=True,
        ):
            rc = main([
                "--backend=rust-analyzer", "cache-status", "--quiet",
            ])
        assert rc == 0
        assert os.environ.get(ENV_VAR) == "1"

    def test_backend_equals_tree_sitter_does_not_set_env(self) -> None:
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
            rc = main(["--backend=tree-sitter", "cache-status", "--quiet"])
        assert rc == 0
        assert ENV_VAR not in os.environ

    def test_no_backend_leaves_env_unchanged(self) -> None:
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
            rc = main(["cache-status", "--quiet"])
        assert rc == 0
        assert ENV_VAR not in os.environ

    def test_trailing_backend_without_arg_does_not_crash(self) -> None:
        """A lone --backend at the end falls through to argparse's error.

        The stripping loop uses ``range(len(argv) - 1)`` so a trailing
        --backend (without a following choice) is not consumed; argparse
        then rejects the incomplete flag with its usual error.
        """
        with pytest.raises(SystemExit):
            main(["cache-status", "--quiet", "--backend"])


class TestBackendIntegrationGate:
    """WI-jinoh / BUG-06: --backend rust-analyzer must error clearly when
    the hypergumbo-lang-rust-analyzer Python integration package is not
    importable. Without this gate the published v4.1.0 distribution
    silently falls through to the tree-sitter backend and the user has
    no surface signal that the SCIP backend never engaged.
    """

    def test_backend_rust_analyzer_errors_when_integration_missing(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        with patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=False,
        ):
            with pytest.raises(SystemExit) as exc_info:
                main(["--backend", "rust-analyzer", "cache-status", "--quiet"])
        assert exc_info.value.code != 0
        # Must NOT have set the env var — otherwise downstream code might
        # still try to engage SCIP and fail less clearly.
        assert ENV_VAR not in os.environ
        err = capsys.readouterr().err
        assert "hypergumbo-lang-rust-analyzer" in err
        assert "not installed" in err

    def test_backend_equals_rust_analyzer_errors_when_integration_missing(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--backend=rust-analyzer form also gates."""
        with patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=False,
        ):
            with pytest.raises(SystemExit) as exc_info:
                main(["--backend=rust-analyzer", "cache-status", "--quiet"])
        assert exc_info.value.code != 0
        assert ENV_VAR not in os.environ

    def test_backend_tree_sitter_does_not_check_integration(self) -> None:
        """--backend tree-sitter never touches the integration gate."""
        with patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=False,
        ):
            with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
                rc = main([
                    "--backend", "tree-sitter", "cache-status", "--quiet",
                ])
        assert rc == 0


class TestBackendBinaryGate:
    """``--backend rust-analyzer`` must error clearly when the rust-analyzer
    binary path resolves but the binary is not actually functional — the
    rustup-proxy-without-component class. Closes the v5.0.0 partial-fix
    gap where engagement silently degraded to tree-sitter without any
    user-visible signal.
    """

    def test_backend_rust_analyzer_errors_when_binary_smoke_fails(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Integration package present, binary smoke-test fails → exit 2."""
        with patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=True,
        ), patch(
            "hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available",
            return_value=False,
        ):
            with pytest.raises(SystemExit) as exc_info:
                main(["--backend", "rust-analyzer", "cache-status", "--quiet"])
        assert exc_info.value.code != 0
        # Must NOT have set the env var — otherwise downstream code might
        # still try to engage SCIP and fail less clearly.
        assert ENV_VAR not in os.environ
        err = capsys.readouterr().err
        assert "rust-analyzer binary is not functional" in err
        assert "rustup component add rust-analyzer" in err

    def test_backend_equals_form_also_gates_on_binary(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--backend=rust-analyzer form also checks the binary smoke-test."""
        with patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=True,
        ), patch(
            "hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available",
            return_value=False,
        ):
            with pytest.raises(SystemExit) as exc_info:
                main(["--backend=rust-analyzer", "cache-status", "--quiet"])
        assert exc_info.value.code != 0
        assert ENV_VAR not in os.environ
        err = capsys.readouterr().err
        assert "rust-analyzer binary is not functional" in err
        assert ENV_VAR not in os.environ

    def test_backend_rust_analyzer_passes_when_integration_present(
        self,
    ) -> None:
        """Happy path: with the integration package importable AND the
        binary smoke-test passing, the env var is set and the subcommand
        runs without error.
        """
        with patch(
            "hypergumbo_core.rust_analyzer_install."
            "is_rust_analyzer_integration_installed",
            return_value=True,
        ), patch(
            "hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available",
            return_value=True,
        ):
            with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
                rc = main([
                    "--backend", "rust-analyzer", "cache-status", "--quiet",
                ])
        assert rc == 0
        assert os.environ.get(ENV_VAR) == "1"
