# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for function summary loading and data structures (ADR-0017 §4b).

Covers YAML loading, summary parsing, callback flow, sanitization effects,
and default-conservative behavior for undeclared functions.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.function_summaries import (
    CallbackFlow,
    FunctionSummary,
    SanitizeEffect,
    clear_summary_cache,
    get_default_summary,
    get_summaries_dir,
    load_function_summaries,
)


class TestFunctionSummaryDataStructures:
    """Test data model objects."""

    def test_function_summary_defaults(self) -> None:
        s = FunctionSummary(function="test")
        assert s.function == "test"
        assert s.param_to_return == {}
        assert s.param_to_self == {}
        assert s.mutates_self is False
        assert s.side_effect is False
        assert s.sanitizes == []
        assert s.callback is None

    def test_function_summary_with_values(self) -> None:
        s = FunctionSummary(
            function="JSON.stringify",
            param_to_return={0: True},
            side_effect=False,
        )
        assert s.param_to_return == {0: True}

    def test_sanitize_effect(self) -> None:
        se = SanitizeEffect(param_index=1, from_taint="plaintext", to_taint="ciphertext")
        assert se.param_index == 1
        assert se.from_taint == "plaintext"
        assert se.to_taint == "ciphertext"

    def test_callback_flow(self) -> None:
        cb = CallbackFlow(
            param_index=1,
            caller_to_callback_args={"0": [0]},
            callback_return_to_outer_return=True,
        )
        assert cb.param_index == 1
        assert cb.callback_return_to_outer_return is True

    def test_callback_flow_defaults(self) -> None:
        cb = CallbackFlow(param_index=0)
        assert cb.caller_to_callback_args == {}
        assert cb.callback_return_to_outer_return is False


class TestGetDefaultSummary:
    """Test default-conservative summary generation."""

    def test_default_summary(self) -> None:
        s = get_default_summary("unknown_func")
        assert s.function == "unknown_func"
        # Conservative: all params 0-9 flow to return
        assert s.param_to_return[0] is True
        assert s.param_to_return[9] is True

    def test_default_summary_has_no_sanitization(self) -> None:
        s = get_default_summary("unknown_func")
        assert s.sanitizes == []
        assert s.callback is None


class TestLoadFunctionSummaries:
    """Test YAML loading of function summaries."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        clear_summary_cache()

    def test_load_builtin_summaries(self) -> None:
        summaries = load_function_summaries()
        assert len(summaries) > 0
        # Should have TypeScript and Rust summaries
        assert "JSON.stringify" in summaries
        assert "Vec::push" in summaries

    def test_json_stringify(self) -> None:
        summaries = load_function_summaries()
        s = summaries["JSON.stringify"]
        assert s.param_to_return == {0: True}

    def test_vec_push(self) -> None:
        summaries = load_function_summaries()
        s = summaries["Vec::push"]
        assert s.mutates_self is True
        assert s.param_to_self == {0: True}

    def test_encrypt_sanitizes(self) -> None:
        summaries = load_function_summaries()
        s = summaries["crypto.subtle.encrypt"]
        assert len(s.sanitizes) == 1
        assert s.sanitizes[0].from_taint == "plaintext"
        assert s.sanitizes[0].to_taint == "ciphertext"

    def test_array_map_callback(self) -> None:
        summaries = load_function_summaries()
        s = summaries["Array.prototype.map"]
        assert s.callback is not None
        assert s.callback.param_index == 1
        assert s.callback.callback_return_to_outer_return is True

    def test_console_log_side_effect(self) -> None:
        summaries = load_function_summaries()
        s = summaries["console.log"]
        assert s.side_effect is True
        assert s.param_to_return == {}

    def test_short_name_indexing(self) -> None:
        summaries = load_function_summaries()
        # "stringify" should be indexed as short name for "JSON.stringify"
        assert "stringify" in summaries

    def test_caching(self) -> None:
        s1 = load_function_summaries()
        s2 = load_function_summaries()
        assert s1 is s2

    def test_custom_dir(self, tmp_path: Path) -> None:
        yaml_content = (
            "summaries:\n"
            "  - function: \"my_func\"\n"
            "    param_to_return: {0: true}\n"
        )
        (tmp_path / "custom.yaml").write_text(yaml_content)
        summaries = load_function_summaries(search_dir=tmp_path)
        assert "my_func" in summaries
        assert summaries["my_func"].param_to_return == {0: True}

    def test_empty_dir(self, tmp_path: Path) -> None:
        summaries = load_function_summaries(search_dir=tmp_path)
        assert summaries == {}

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        summaries = load_function_summaries(search_dir=tmp_path / "nonexistent")
        assert summaries == {}

    def test_yaml_without_summaries_key(self, tmp_path: Path) -> None:
        (tmp_path / "bad.yaml").write_text("other_key: value\n")
        summaries = load_function_summaries(search_dir=tmp_path)
        assert summaries == {}

    def test_empty_yaml(self, tmp_path: Path) -> None:
        (tmp_path / "empty.yaml").write_text("")
        summaries = load_function_summaries(search_dir=tmp_path)
        assert summaries == {}

    def test_summaries_dir(self) -> None:
        d = get_summaries_dir()
        assert d.is_dir()
        assert any(d.glob("*.yaml"))

    def test_all_sanitize_fields(self, tmp_path: Path) -> None:
        yaml_content = (
            "summaries:\n"
            "  - function: \"encrypt\"\n"
            "    param_to_return: {1: true}\n"
            "    sanitizes:\n"
            "      1:\n"
            "        from: plaintext\n"
            "        to: ciphertext\n"
        )
        (tmp_path / "san.yaml").write_text(yaml_content)
        summaries = load_function_summaries(search_dir=tmp_path)
        s = summaries["encrypt"]
        assert len(s.sanitizes) == 1
        assert s.sanitizes[0].param_index == 1

    def test_callback_without_return(self, tmp_path: Path) -> None:
        yaml_content = (
            "summaries:\n"
            "  - function: \"forEach\"\n"
            "    param_to_return: {}\n"
            "    callback:\n"
            "      param_index: 1\n"
            "      caller_to_callback_args:\n"
            "        \"0\": [0]\n"
        )
        (tmp_path / "cb.yaml").write_text(yaml_content)
        summaries = load_function_summaries(search_dir=tmp_path)
        s = summaries["forEach"]
        assert s.callback is not None
        assert s.callback.callback_return_to_outer_return is False

    def test_mutates_self_and_param_to_self(self, tmp_path: Path) -> None:
        yaml_content = (
            "summaries:\n"
            "  - function: \"push\"\n"
            "    param_to_self: {0: true}\n"
            "    mutates_self: true\n"
        )
        (tmp_path / "mut.yaml").write_text(yaml_content)
        summaries = load_function_summaries(search_dir=tmp_path)
        s = summaries["push"]
        assert s.mutates_self is True
        assert s.param_to_self == {0: True}
