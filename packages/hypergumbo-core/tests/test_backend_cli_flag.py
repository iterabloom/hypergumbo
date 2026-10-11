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

    def test_backend_tree_sitter_records_an_explicit_off(self) -> None:
        """Was: asserted tree-sitter set NOTHING. Premise kept, conclusion inverted.

        Setting nothing is what made the advertised opt-out inert -- with
        HYPERGUMBO_RUST_ANALYZER=1 already exported, a no-op leaves the
        backend on and the analysed repo's build.rs runs. ADR-0045 ruling 4
        makes the flag outrank the variable in BOTH directions, so the
        negative arm has to write something the gate can read as a refusal.
        """
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
            rc = main(["--backend", "tree-sitter", "cache-status", "--quiet"])
        assert rc == 0
        assert os.environ.get(ENV_VAR) == "0"

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

    def test_backend_equals_tree_sitter_records_an_explicit_off(self) -> None:
        """The ``=``-form must not diverge from the space-form (see above)."""
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
            rc = main(["--backend=tree-sitter", "cache-status", "--quiet"])
        assert rc == 0
        assert os.environ.get(ENV_VAR) == "0"

    def test_no_backend_leaves_env_unchanged(self) -> None:
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
            rc = main(["cache-status", "--quiet"])
        assert rc == 0
        assert ENV_VAR not in os.environ

    def test_trailing_backend_without_arg_is_a_clear_usage_error(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A lone --backend at the end exits 2 with argparse's own wording.

        It must neither crash (IndexError reading the missing value) nor be
        misreported as an unrecognised argument of the subcommand.
        """
        with pytest.raises(SystemExit) as exc:
            main(["cache-status", "--quiet", "--backend"])
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "argument --backend: expected one argument" in err

    def test_backend_followed_by_a_flag_is_missing_its_value(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``--backend --quiet`` does not consume ``--quiet`` as the choice."""
        with pytest.raises(SystemExit) as exc:
            main(["cache-status", "--backend", "--quiet"])
        assert exc.value.code == 2
        assert "expected one argument" in capsys.readouterr().err

    def test_backend_equals_form_as_the_final_token(self) -> None:
        """``--backend=X`` needs no following token, so it works last too.

        The pre-scan used to iterate ``range(len(argv) - 1)`` and never
        examined the last token, so ``hypergumbo catalog
        --backend=tree-sitter`` reached argparse unstripped and exited 2
        with "unrecognized arguments".
        """
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
            rc = main(["cache-status", "--quiet", "--backend=tree-sitter"])
        assert rc == 0
        assert os.environ.get(ENV_VAR) == "0"

    def test_backend_equals_form_as_the_only_token(self) -> None:
        """A one-token argv is also scanned (default sketch mode path)."""
        with patch("hypergumbo_core.cli._apply_backend_choice") as apply, \
                patch("hypergumbo_core.cli.cmd_sketch", return_value=0):
            rc = main(["--backend=tree-sitter"])
        assert rc == 0
        apply.assert_called_once_with("tree-sitter")

    @pytest.mark.parametrize(
        "argv",
        [
            ["--backend", "bogus", "cache-status", "--quiet"],
            ["cache-status", "--backend", "bogus", "--quiet"],
            ["--backend=bogus", "cache-status", "--quiet"],
            ["cache-status", "--quiet", "--backend=bogus"],
            ["cache-status", "--quiet", "--backend="],
        ],
    )
    def test_unknown_choice_is_rejected_in_every_position(
        self, argv: list[str], capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The pre-scan strips the flag before argparse sees it, so it must
        enforce the parser's ``choices=`` itself; it used to swallow any
        value and run the command."""
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0) as cmd:
            with pytest.raises(SystemExit) as exc:
                main(argv)
        assert exc.value.code == 2
        assert "argument --backend: invalid choice" in capsys.readouterr().err
        cmd.assert_not_called()

    def test_tokens_after_double_dash_are_not_scanned(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """After ``--`` every token is positional, so the pre-scan stops."""
        with pytest.raises(SystemExit) as exc:
            main(["cache-status", "--", "--backend=tree-sitter"])
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "unrecognized arguments" in err
        assert ENV_VAR not in os.environ


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


class TestTheAdvertisedOptOutActuallyOptsOut:
    """The live defect, at the layer a user meets it.

    Reproduced end-to-end before the fix on an authored Rust fixture: with
    ``HYPERGUMBO_RUST_ANALYZER=1`` exported, ``--backend tree-sitter``
    produced 10 nodes carrying ``origin=['scip']`` and the run printed
    "rust-analyzer backend ACTIVE" -- i.e. the SCIP backend ran, having
    indexed (and therefore executed) the workspace's build scripts, after the
    tool's own warning told the user this flag would prevent that.

    These assert on the ENV TRANSPORT rather than on a real analysis because
    the end-to-end repro needs a Rust toolchain; the gate's own tests cover
    the consumption side, so between them the path is closed. The gap is
    named rather than papered over: nothing here proves the two halves are
    wired to each other, which is what the fixture run does by hand.
    """

    def test_tree_sitter_flag_overrides_an_exported_opt_in(self) -> None:
        os.environ[ENV_VAR] = "1"
        try:
            with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
                rc = main(["--backend", "tree-sitter", "cache-status", "--quiet"])
            assert rc == 0
            assert os.environ.get(ENV_VAR) == "0"
        finally:
            os.environ.pop(ENV_VAR, None)

    def test_rust_analyzer_flag_overrides_an_exported_opt_out(self) -> None:
        """The symmetric direction, so the fix is a precedence rule and not a
        special case that only ever turns things off."""
        os.environ[ENV_VAR] = "0"
        try:
            with patch(
                "hypergumbo_core.cli.cmd_cache_status", return_value=0,
            ), patch(
                "hypergumbo_core.rust_analyzer_install."
                "is_rust_analyzer_integration_installed",
                return_value=True,
            ), patch(
                "hypergumbo_core.rust_analyzer_install."
                "is_rust_analyzer_available",
                return_value=True,
            ):
                rc = main([
                    "--backend", "rust-analyzer", "cache-status", "--quiet",
                ])
            assert rc == 0
            assert os.environ.get(ENV_VAR) == "1"
        finally:
            os.environ.pop(ENV_VAR, None)

    def test_an_unrecognised_backend_leaves_the_opt_in_alone(self) -> None:
        """`--backend nonsense` must not be read as a refusal.

        The pre-scan in ``main`` now rejects an unknown choice with exit 2
        (it used to swallow it and run the command). The precedence property
        still matters: rejecting must happen before anything is written, so a
        lower tier's decision is left standing rather than clobbered.
        """
        os.environ[ENV_VAR] = "1"
        try:
            with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
                with pytest.raises(SystemExit) as exc:
                    main(["--backend", "nonsense", "cache-status", "--quiet"])
            assert exc.value.code == 2
            assert os.environ.get(ENV_VAR) == "1"
        finally:
            os.environ.pop(ENV_VAR, None)


PY_ENV_VAR = "HYPERGUMBO_SCIP_PYTHON"


class TestTheScipPythonArmOfTheFlag:
    """WI-nanom: ``--backend scip-python`` rides the same flag. It is gated on
    the wrapper package and the binary exactly as the Rust arm is, writes
    ``HYPERGUMBO_SCIP_PYTHON`` as its transport, and ``tree-sitter`` is an
    explicit OFF for both backends at once."""

    @pytest.fixture(autouse=True)
    def _scrub_python_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PY_ENV_VAR, raising=False)

    def test_scip_python_is_an_accepted_choice(self) -> None:
        assert build_parser().parse_args(["--backend", "scip-python", "cache-status"]).backend == "scip-python"

    def test_missing_integration_exits_two_naming_the_package(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("hypergumbo_core.scip_python_install.is_scip_python_integration_installed", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                main(["--backend", "scip-python", "cache-status", "--quiet"])
        assert exc_info.value.code == 2
        assert PY_ENV_VAR not in os.environ
        err = capsys.readouterr().err
        assert "hypergumbo-lang-scip-python" in err and "not installed" in err

    def test_missing_binary_exits_two_with_the_npm_line(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("hypergumbo_core.scip_python_install.is_scip_python_integration_installed", return_value=True), \
             patch("hypergumbo_core.scip_python_install.is_scip_python_available", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                main(["--backend", "scip-python", "cache-status", "--quiet"])
        assert exc_info.value.code == 2
        assert PY_ENV_VAR not in os.environ
        assert "npm install -g @sourcegraph/scip-python@0.6.6" in capsys.readouterr().err

    def test_present_and_available_sets_the_transport(self) -> None:
        with patch("hypergumbo_core.scip_python_install.is_scip_python_integration_installed", return_value=True), \
             patch("hypergumbo_core.scip_python_install.is_scip_python_available", return_value=True), \
             patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
            rc = main(["--backend", "scip-python", "cache-status", "--quiet"])
        assert rc == 0
        assert os.environ.get(PY_ENV_VAR) == "1"
        assert ENV_VAR not in os.environ  # the Rust arm was not asked about

    def test_tree_sitter_turns_both_backends_off(self) -> None:
        with patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
            rc = main(["--backend", "tree-sitter", "cache-status", "--quiet"])
        assert rc == 0
        assert os.environ.get(ENV_VAR) == "0" and os.environ.get(PY_ENV_VAR) == "0"

    def test_the_rust_flag_leaves_the_python_backend_alone(self) -> None:
        with patch("hypergumbo_core.rust_analyzer_install.is_rust_analyzer_integration_installed", return_value=True), \
             patch("hypergumbo_core.rust_analyzer_install.is_rust_analyzer_available", return_value=True), \
             patch("hypergumbo_core.cli.cmd_cache_status", return_value=0):
            main(["--backend", "rust-analyzer", "cache-status", "--quiet"])
        assert os.environ.get(ENV_VAR) == "1" and PY_ENV_VAR not in os.environ
