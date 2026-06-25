# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for 'hypergumbo config show <lang>' subcommand (WI-siran)."""
import json
from io import StringIO
from unittest.mock import patch

import pytest

from hypergumbo_core.cli import cmd_config, main


class FakeArgs:
    """Minimal namespace for testing command functions."""
    pass


class TestCmdConfig:
    """Tests for cmd_config (WI-siran)."""

    def test_known_language_returns_config(self) -> None:
        """Config for a known language (go) returns non-empty JSON."""
        args = FakeArgs()
        args.language = "go"
        args.format = "json"

        buf = StringIO()
        with patch("sys.stdout", buf):
            result = cmd_config(args)

        assert result == 0
        data = json.loads(buf.getvalue())
        assert "dataflow_patterns" in data
        assert data["dataflow_patterns"] is not None

    def test_known_language_io_primitives(self) -> None:
        """Config for go includes io_primitives section."""
        args = FakeArgs()
        args.language = "go"
        args.format = "json"

        buf = StringIO()
        with patch("sys.stdout", buf):
            cmd_config(args)

        data = json.loads(buf.getvalue())
        assert "io_primitives" in data
        assert data["io_primitives"] is not None

    def test_non_language_name_rejected(self) -> None:
        """INV-gufod: a non-language name (not in the language catalog) is
        rejected with exit 2 and an error — not silently accepted as an
        all-null config at exit 0."""
        args = FakeArgs()
        args.language = "brainfuck"  # not a known language
        args.format = "json"

        buf = StringIO()
        err_buf = StringIO()
        with patch("sys.stdout", buf), patch("sys.stderr", err_buf):
            result = cmd_config(args)

        assert result == 2
        assert buf.getvalue() == ""  # no JSON emitted on rejection
        assert "not a known language" in err_buf.getvalue().lower()

    def test_framework_linker_name_rejected(self) -> None:
        """INV-gufod: a framework/linker name (the original repro) is rejected,
        not treated as a language."""
        args = FakeArgs()
        args.language = "airflow-framework-dispatch-linker"
        args.format = "json"

        err_buf = StringIO()
        with patch("sys.stdout", StringIO()), patch("sys.stderr", err_buf):
            result = cmd_config(args)

        assert result == 2
        assert "not a known language" in err_buf.getvalue().lower()

    def test_did_you_mean_suggestion_for_near_miss(self) -> None:
        """INV-gufod: a near-miss typo of a real language gets a 'Did you mean'
        suggestion (exercises the difflib branch)."""
        args = FakeArgs()
        args.language = "pythonn"  # typo of python
        args.format = "json"

        err_buf = StringIO()
        with patch("sys.stdout", StringIO()), patch("sys.stderr", err_buf):
            result = cmd_config(args)

        assert result == 2
        err = err_buf.getvalue().lower()
        assert "did you mean" in err and "python" in err

    def test_known_language_without_config_warns(self) -> None:
        """A real language with no config yaml is still VALID (exit 0): it
        returns all-null sections with a warning. Only non-languages error."""
        args = FakeArgs()
        args.language = "agda"  # a known language with no config yaml
        args.format = "json"

        buf = StringIO()
        err_buf = StringIO()
        with patch("sys.stdout", buf), patch("sys.stderr", err_buf):
            result = cmd_config(args)

        assert result == 0
        data = json.loads(buf.getvalue())
        assert data["dataflow_patterns"] is None
        assert data["io_primitives"] is None
        assert "no configuration" in err_buf.getvalue().lower()

    def test_text_format(self) -> None:
        """Text format outputs human-readable summary."""
        args = FakeArgs()
        args.language = "python"
        args.format = "text"

        buf = StringIO()
        with patch("sys.stdout", buf):
            result = cmd_config(args)

        assert result == 0
        output = buf.getvalue()
        assert "python" in output.lower()
        assert "dataflow_patterns" in output

    def test_yaml_format(self) -> None:
        """YAML format outputs valid YAML."""
        args = FakeArgs()
        args.language = "go"
        args.format = "yaml"

        buf = StringIO()
        with patch("sys.stdout", buf):
            result = cmd_config(args)

        assert result == 0
        output = buf.getvalue()
        assert "dataflow_patterns:" in output

    def test_missing_sections_degrade_gracefully(self) -> None:
        """Language with partial config (e.g., only dataflow, no io) works."""
        args = FakeArgs()
        # lua has dataflow_patterns but no io_primitives
        args.language = "lua"
        args.format = "json"

        buf = StringIO()
        with patch("sys.stdout", buf):
            result = cmd_config(args)

        assert result == 0
        data = json.loads(buf.getvalue())
        assert data["dataflow_patterns"] is not None
        assert data["io_primitives"] is None

    def test_config_subcommand_in_main(self) -> None:
        """'config show go' is recognized by main()."""
        buf = StringIO()
        with patch("sys.stdout", buf):
            result = main(["config", "go", "--format", "json"])

        assert result == 0
        data = json.loads(buf.getvalue())
        assert "dataflow_patterns" in data

    def test_yaml_parse_error_degrades_gracefully(self) -> None:
        """Corrupted YAML produces None for that section."""
        import yaml

        args = FakeArgs()
        args.language = "go"
        args.format = "json"

        original_safe_load = yaml.safe_load
        call_count = 0

        def mock_safe_load(text):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("mock YAML parse error")
            return original_safe_load(text)

        buf = StringIO()
        with patch.object(yaml, "safe_load", side_effect=mock_safe_load), \
             patch("sys.stdout", buf):
            result = cmd_config(args)

        assert result == 0
        data = json.loads(buf.getvalue())
        # The first section (dataflow_patterns) should be None due to error
        assert data["dataflow_patterns"] is None
