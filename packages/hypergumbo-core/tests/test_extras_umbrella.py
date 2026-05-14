# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the consolidated add-extras / remove-extras umbrella (WI-josif).

This module supersedes the old install-extras-specific tests; the
``install-extras`` / ``uninstall-extras`` subcommands were hard-removed in
favor of the single ``add-extras`` / ``remove-extras`` umbrella driven by
the ``_extras_components`` declarative table. Coverage:

- ``cmd_add_extras`` (--check, --skip, install loop)
- ``cmd_remove_extras`` (--skip, uninstall loop)
- ``_extras_components`` table shape (rows, callable contract)
- ``_pretty_extras_name`` display formatting
- The grammars-row helpers (``_is_grammars_available``, ``_install_grammars``,
  ``_uninstall_grammars``)
- ``_install_rust_analyzer_with_bug06_gate`` (BUG-06 gate)
- The shared embedding adapters (``_install_embeddings_impl``,
  ``_uninstall_embeddings_impl``) with subprocess.run mocked
- A regression test asserting the legacy ``install-extras`` /
  ``uninstall-extras`` subcommands are no longer registered with argparse

Most umbrella tests mock the ``_extras_components`` table itself with
hand-crafted row tuples so each scenario is precisely shaped without
shelling out to pip / rustup / gitleaks. The embedding-adapter tests
patch ``subprocess.run`` because they verify the adapter's own pip
invocation behavior.
"""
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
# cmd_add_extras --check
# ---------------------------------------------------------------------------


class TestAddExtrasCheck:
    def test_all_present_returns_zero(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_add_extras

        args = Namespace(check=True, skip=None, quiet=False)
        components = [
            ("grammars", lambda: True, None, None),
            ("gitleaks", lambda: True, None, None),
            ("embeddings", lambda: True, None, None),
            ("rust-analyzer", lambda: True, None, None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_add_extras(args)
        assert rc == 0
        out = capsys.readouterr().out
        for name in ("grammars", "gitleaks", "embeddings", "rust-analyzer"):
            assert name in out
        assert "✓" in out  # check mark for installed

    def test_one_missing_returns_one(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_add_extras

        args = Namespace(check=True, skip=None, quiet=False)
        components = [
            ("grammars", lambda: True, None, None),
            ("gitleaks", lambda: True, None, None),
            ("embeddings", lambda: False, None, None),
            ("rust-analyzer", lambda: True, None, None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_add_extras(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "embeddings:" in out
        assert "✗" in out  # cross mark for missing

    def test_skip_excludes_from_table(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_add_extras

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
            rc = cmd_add_extras(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "gitleaks" not in out
        assert "embeddings" not in out
        assert "rust-analyzer" in out

    def test_skip_accepts_empty_segments(self) -> None:
        """``--skip 'gitleaks,,'`` should not parse a stray empty entry."""
        from hypergumbo_core.cli import cmd_add_extras

        args = Namespace(check=True, skip="gitleaks,,", quiet=False)
        components = [
            ("gitleaks", lambda: True, None, None),
            ("embeddings", lambda: True, None, None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_add_extras(args)
        assert rc == 0


# ---------------------------------------------------------------------------
# cmd_add_extras (install path)
# ---------------------------------------------------------------------------


class TestAddExtrasInstall:
    def test_skips_already_installed_components(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_add_extras

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
            rc = cmd_add_extras(args)
        assert rc == 0
        assert called == []
        assert "already installed. Skipping." in capsys.readouterr().out

    def test_runs_all_missing_installers(self) -> None:
        from hypergumbo_core.cli import cmd_add_extras

        called: list[str] = []

        def _make_install(name: str):
            def _install(quiet: bool = False) -> bool:
                called.append(name)
                return True
            return _install

        args = Namespace(check=False, skip=None, quiet=True)
        components = [
            ("grammars", lambda: False, _make_install("grammars"), None),
            ("gitleaks", lambda: False, _make_install("gitleaks"), None),
            ("embeddings", lambda: False, _make_install("embeddings"), None),
            ("rust-analyzer", lambda: False, _make_install("rust-analyzer"), None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_add_extras(args)
        assert rc == 0
        assert called == ["grammars", "gitleaks", "embeddings", "rust-analyzer"]

    def test_install_failure_returns_one_but_continues(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_add_extras

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
            rc = cmd_add_extras(args)
        assert rc == 1
        # embeddings still ran despite gitleaks failing — best-effort umbrella.
        assert called == ["gitleaks", "embeddings"]

    def test_section_headers_in_verbose_mode(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_add_extras

        args = Namespace(check=False, skip=None, quiet=False)
        components = [
            ("grammars", lambda: True, None, None),
            ("rust-analyzer", lambda: True, None, None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_add_extras(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "=== Grammars ===" in out
        assert "=== Rust analyzer ===" in out
        assert "=== Summary ===" in out
        assert "All extras installed" in out

    def test_quiet_suppresses_section_headers(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_add_extras

        args = Namespace(check=False, skip=None, quiet=True)
        components = [
            ("gitleaks", lambda: True, None, None),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_add_extras(args)
        assert rc == 0
        assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# cmd_remove_extras
# ---------------------------------------------------------------------------


class TestRemoveExtras:
    def test_all_succeed_returns_zero(self) -> None:
        from hypergumbo_core.cli import cmd_remove_extras

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
            rc = cmd_remove_extras(args)
        assert rc == 0
        assert called == ["gitleaks", "embeddings"]

    def test_skips_when_not_installed(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_remove_extras

        called: list[str] = []

        def _uninstall_never(quiet: bool = False) -> bool:  # pragma: no cover
            called.append("never")
            return True

        args = Namespace(skip=None, quiet=False)
        components = [
            ("gitleaks", lambda: False, None, _uninstall_never),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_remove_extras(args)
        assert rc == 0
        assert called == []
        assert "not installed. Skipping." in capsys.readouterr().out

    def test_any_failure_returns_one(self) -> None:
        from hypergumbo_core.cli import cmd_remove_extras

        args = Namespace(skip=None, quiet=True)
        components = [
            ("gitleaks", lambda: True, None, lambda quiet=False: False),
            ("embeddings", lambda: True, None, lambda quiet=False: True),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_remove_extras(args)
        assert rc == 1

    def test_skip_excludes_components(self) -> None:
        from hypergumbo_core.cli import cmd_remove_extras

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
            rc = cmd_remove_extras(args)
        assert rc == 0
        assert called == ["embeddings"]

    def test_summary_in_verbose_mode(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import cmd_remove_extras

        args = Namespace(skip=None, quiet=False)
        components = [
            ("gitleaks", lambda: True, None, lambda quiet=False: True),
        ]
        with patch(
            "hypergumbo_core.cli._extras_components",
            return_value=components,
        ):
            rc = cmd_remove_extras(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "=== Gitleaks ===" in out
        assert "=== Summary ===" in out
        assert "Run 'hypergumbo add-extras' to reinstall." in out


# ---------------------------------------------------------------------------
# _extras_components shape
# ---------------------------------------------------------------------------


class TestExtrasComponentsTable:
    def test_returns_four_rows_with_expected_names(self) -> None:
        from hypergumbo_core.cli import _extras_components

        rows = _extras_components()
        names = [row[0] for row in rows]
        assert names == ["grammars", "gitleaks", "embeddings", "rust-analyzer"]
        for name, is_available, install, uninstall in rows:
            assert callable(is_available), name
            assert callable(install), name
            assert callable(uninstall), name


# ---------------------------------------------------------------------------
# _pretty_extras_name
# ---------------------------------------------------------------------------


class TestPrettyExtrasName:
    def test_capitalizes_simple_name(self) -> None:
        from hypergumbo_core.cli import _pretty_extras_name
        assert _pretty_extras_name("grammars") == "Grammars"
        assert _pretty_extras_name("gitleaks") == "Gitleaks"
        assert _pretty_extras_name("embeddings") == "Embeddings"

    def test_replaces_hyphen_with_space(self) -> None:
        from hypergumbo_core.cli import _pretty_extras_name
        assert _pretty_extras_name("rust-analyzer") == "Rust analyzer"


# ---------------------------------------------------------------------------
# Grammars-row helpers
# ---------------------------------------------------------------------------


class TestGrammarsHelpers:
    def test_is_grammars_available_all_present(self) -> None:
        from hypergumbo_core.cli import _is_grammars_available
        with patch(
            "hypergumbo_core.cli.check_grammar_availability",
            return_value={"lean": True, "wolfram": True},
        ):
            assert _is_grammars_available() is True

    def test_is_grammars_available_some_missing(self) -> None:
        from hypergumbo_core.cli import _is_grammars_available
        with patch(
            "hypergumbo_core.cli.check_grammar_availability",
            return_value={"lean": True, "wolfram": False},
        ):
            assert _is_grammars_available() is False

    def test_install_grammars_success(self) -> None:
        from hypergumbo_core.cli import _install_grammars
        with patch(
            "hypergumbo_core.cli.build_all_grammars",
            return_value={"lean": True, "wolfram": True},
        ):
            assert _install_grammars(quiet=True) is True

    def test_install_grammars_partial_failure_warns_and_returns_false(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _install_grammars
        with patch(
            "hypergumbo_core.cli.build_all_grammars",
            return_value={"lean": False, "wolfram": True},
        ):
            assert _install_grammars(quiet=True) is False
        err = capsys.readouterr().err
        assert "Failed to build grammars" in err
        assert "lean" in err

    def test_uninstall_grammars_success(self) -> None:
        from hypergumbo_core.cli import _uninstall_grammars

        with patch("subprocess.run", return_value=_Completed()):
            assert _uninstall_grammars(quiet=True) is True

    def test_uninstall_grammars_success_emits_chatter(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _uninstall_grammars

        with patch("subprocess.run", return_value=_Completed()):
            assert _uninstall_grammars() is True
        out = capsys.readouterr().out
        assert "Removing source-built grammars" in out
        assert "Done" in out

    def test_uninstall_grammars_targets_three_source_grammars(self) -> None:
        from hypergumbo_core.cli import _uninstall_grammars

        with patch("subprocess.run", return_value=_Completed()) as run:
            _uninstall_grammars(quiet=True)
        cmd = run.call_args.args[0]
        assert "pip" in cmd
        assert "uninstall" in cmd
        assert "-y" in cmd
        assert "tree-sitter-lean" in cmd
        assert "tree-sitter-wolfram" in cmd
        assert "tree-sitter-circom" in cmd

    def test_uninstall_grammars_timeout(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _uninstall_grammars

        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="pip", timeout=1)

        with patch("subprocess.run", side_effect=_raise):
            assert _uninstall_grammars(quiet=True) is False
        assert "Error uninstalling grammars" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Holistic rust-analyzer availability (binary AND integration package)
# ---------------------------------------------------------------------------


class TestRustAnalyzerFullyAvailable:
    """``_is_rust_analyzer_fully_available`` is the rust-analyzer row's
    ``is_available`` predicate. It must report False whenever ``--backend
    rust-analyzer`` would hit the BUG-06 runtime gate, so ``add-extras
    --check`` does not show a misleadingly green ``✓ installed`` for a
    binary-present-integration-missing edge case (e.g. system-package
    manager rustup install, or post-extra-uninstall residual binary)."""

    def test_true_when_both_present(self) -> None:
        from hypergumbo_core.cli import _is_rust_analyzer_fully_available
        with patch("hypergumbo_core.cli.is_rust_analyzer_available", return_value=True):
            with patch(
                "hypergumbo_core.cli.is_rust_analyzer_integration_installed",
                return_value=True,
            ):
                assert _is_rust_analyzer_fully_available() is True

    def test_false_when_binary_missing(self) -> None:
        from hypergumbo_core.cli import _is_rust_analyzer_fully_available
        with patch("hypergumbo_core.cli.is_rust_analyzer_available", return_value=False):
            with patch(
                "hypergumbo_core.cli.is_rust_analyzer_integration_installed",
                return_value=True,
            ):
                assert _is_rust_analyzer_fully_available() is False

    def test_false_when_integration_missing(self) -> None:
        """The narrowed asymmetry the holistic check closes: rustup binary
        present (e.g. system package manager) but integration package
        missing — the user-facing scenario where ``--backend rust-analyzer``
        would hit the BUG-06 runtime gate."""
        from hypergumbo_core.cli import _is_rust_analyzer_fully_available
        with patch("hypergumbo_core.cli.is_rust_analyzer_available", return_value=True):
            with patch(
                "hypergumbo_core.cli.is_rust_analyzer_integration_installed",
                return_value=False,
            ):
                assert _is_rust_analyzer_fully_available() is False

    def test_false_when_both_missing(self) -> None:
        from hypergumbo_core.cli import _is_rust_analyzer_fully_available
        with patch("hypergumbo_core.cli.is_rust_analyzer_available", return_value=False):
            with patch(
                "hypergumbo_core.cli.is_rust_analyzer_integration_installed",
                return_value=False,
            ):
                assert _is_rust_analyzer_fully_available() is False


# ---------------------------------------------------------------------------
# BUG-06-gated rust-analyzer install
# ---------------------------------------------------------------------------


class TestRustAnalyzerInstallGate:
    def test_calls_install_when_integration_present(self) -> None:
        from hypergumbo_core.cli import _install_rust_analyzer_with_bug06_gate
        with patch(
            "hypergumbo_core.cli.is_rust_analyzer_integration_installed",
            return_value=True,
        ):
            with patch(
                "hypergumbo_core.cli.install_rust_analyzer", return_value=True,
            ) as mock_install:
                assert _install_rust_analyzer_with_bug06_gate(quiet=True) is True
        mock_install.assert_called_once_with(quiet=True)

    def test_skips_install_when_integration_missing(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _install_rust_analyzer_with_bug06_gate
        with patch(
            "hypergumbo_core.cli.is_rust_analyzer_integration_installed",
            return_value=False,
        ):
            with patch(
                "hypergumbo_core.cli.install_rust_analyzer",
            ) as mock_install:
                # Returns True so the umbrella treats it as a soft-skip,
                # not an install failure.
                assert _install_rust_analyzer_with_bug06_gate(quiet=False) is True
        mock_install.assert_not_called()
        out = capsys.readouterr().out
        assert "hypergumbo[rust-analyzer]" in out
        # The skip message must include `--force` so the user can
        # actually unblock themselves on an existing pipx install
        # (without --force, pipx silently no-ops "already installed",
        # and the install pointer becomes a dead end).
        assert "--force" in out
        # And it should mention `pipx inject` as an alternative for
        # users who want to keep their current install pinned.
        assert "pipx inject" in out

    def test_skip_message_suppressed_in_quiet_mode(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from hypergumbo_core.cli import _install_rust_analyzer_with_bug06_gate
        with patch(
            "hypergumbo_core.cli.is_rust_analyzer_integration_installed",
            return_value=False,
        ):
            with patch(
                "hypergumbo_core.cli.install_rust_analyzer",
            ) as mock_install:
                assert _install_rust_analyzer_with_bug06_gate(quiet=True) is True
        mock_install.assert_not_called()
        assert capsys.readouterr().out == ""

    def test_propagates_install_failure(self) -> None:
        from hypergumbo_core.cli import _install_rust_analyzer_with_bug06_gate
        with patch(
            "hypergumbo_core.cli.is_rust_analyzer_integration_installed",
            return_value=True,
        ):
            with patch(
                "hypergumbo_core.cli.install_rust_analyzer", return_value=False,
            ):
                assert _install_rust_analyzer_with_bug06_gate(quiet=True) is False


# ---------------------------------------------------------------------------
# Embeddings adapters (subprocess.run mocked, no real pip invocation)
# ---------------------------------------------------------------------------


class TestEmbeddingsAdapters:
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


# ---------------------------------------------------------------------------
# Regression: legacy install-extras / uninstall-extras subcommands removed
# ---------------------------------------------------------------------------


class TestLegacyUmbrellaRemoved:
    """The pre-WI-josif install-extras / uninstall-extras umbrellas were
    hard-removed. Ensure they are no longer registered as subcommands so
    we don't silently re-introduce the duplicate sprawl.

    Tests build the argparse parser directly (no subprocess shell-out) and
    inspect its known choices — argparse exits with SystemExit(2) when an
    unknown subcommand is parsed."""

    def test_install_extras_subcommand_unrecognized(self) -> None:
        from hypergumbo_core.cli import build_parser

        parser = build_parser()
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(["install-extras"])
        assert excinfo.value.code == 2

    def test_uninstall_extras_subcommand_unrecognized(self) -> None:
        from hypergumbo_core.cli import build_parser

        parser = build_parser()
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(["uninstall-extras"])
        assert excinfo.value.code == 2

    def test_add_extras_and_remove_extras_remain_registered(self) -> None:
        """Positive control: confirm the surviving subcommands still parse."""
        from hypergumbo_core.cli import build_parser

        parser = build_parser()
        # Both should parse without raising; --help would exit 0, but here
        # we just want to verify the subcommand exists in the choice set.
        args = parser.parse_args(["add-extras"])
        assert args.func.__name__ == "cmd_add_extras"
        args = parser.parse_args(["remove-extras"])
        assert args.func.__name__ == "cmd_remove_extras"
