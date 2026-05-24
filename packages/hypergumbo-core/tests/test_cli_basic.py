# SPDX-License-Identifier: AGPL-3.0-or-later
import argparse
import logging
import sys

import pytest

from hypergumbo_core.cli import build_parser, main


def test_version_flag_prints_version_and_exits(capsys):
    """Test that --version prints the hypergumbo meta-package version."""
    hypergumbo = pytest.importorskip(
        "hypergumbo", reason="requires hypergumbo meta-package"
    )

    parser = build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])

    assert exc.value.code == 0

    out, err = capsys.readouterr()
    assert hypergumbo.__version__ in out
    assert "hypergumbo" in out


def test_debug_flag_configures_logging(tmp_path, monkeypatch):
    """--debug flag enables DEBUG level logging."""
    # Create a minimal repo
    (tmp_path / "test.py").write_text("x = 1")

    # Track if basicConfig was called with DEBUG level
    original_basicConfig = logging.basicConfig
    config_calls = []

    def mock_basicConfig(**kwargs):
        config_calls.append(kwargs)
        # Don't actually configure logging (would affect other tests)

    monkeypatch.setattr(logging, "basicConfig", mock_basicConfig)

    # Run with --debug flag (will fail because no proper repo, but that's ok)
    # We just need to verify logging was configured
    try:
        main(["--debug", "sketch", str(tmp_path), "-t", "100"])
    except Exception:
        pass  # Expected - minimal repo won't fully work

    # Verify basicConfig was called with DEBUG level
    assert len(config_calls) == 1
    assert config_calls[0]["level"] == logging.DEBUG


def test_main_uses_sys_argv_when_argv_not_provided(tmp_path, monkeypatch):
    """main() uses sys.argv[1:] when argv parameter is None (covers cli.py:3471).

    This tests the CLI entry point code path where main() is called without
    an explicit argv argument, causing it to read from sys.argv.
    """
    # Create a minimal repo
    (tmp_path / "test.py").write_text("x = 1")

    # Set sys.argv to simulate CLI invocation
    monkeypatch.setattr(sys, "argv", ["hypergumbo", "sketch", str(tmp_path), "-t", "100"])

    # Call main() without argv - it should use sys.argv[1:]
    result = main()  # No argv parameter

    assert result == 0


def test_usage_metavar_includes_every_registered_subparser():
    """WI-zunos: every registered subparser must appear in the usage line.

    Previously the metavar was a hardcoded string that drifted as new
    subcommands were added (config, dead-code-maybe, verify-claims,
    cache-status, cache-clear were all silently omitted). The fix
    builds the metavar dynamically from the registered subparsers,
    so every command shows up in `hypergumbo --help`.
    """
    parser = build_parser()
    sub_action = next(
        a for a in parser._actions
        if isinstance(a, argparse._SubParsersAction)
    )
    metavar = sub_action.metavar
    assert metavar is not None
    # Every registered subparser name appears in the metavar.
    for name in sub_action.choices:
        assert name in metavar, f"{name!r} missing from sub.metavar={metavar!r}"
    # Explicit guards for the five that were previously missing.
    for previously_missing in (
        "config", "dead-code-maybe", "verify-claims",
        "cache-status", "cache-clear",
    ):
        assert previously_missing in metavar, (
            f"{previously_missing!r} missing from metavar — WI-zunos regression"
        )
