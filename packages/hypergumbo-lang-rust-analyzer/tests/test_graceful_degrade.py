# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for :mod:`hypergumbo_lang_rust_analyzer.graceful_degrade` (WI-nohah)."""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import pytest
from google.protobuf.message import DecodeError

from hypergumbo_core.ir import Edge, Span, Symbol
from hypergumbo_lang_rust_analyzer.graceful_degrade import (
    _reset_logged_fallback_for_tests,
    try_analyze_with_rust_analyzer,
)
from hypergumbo_lang_rust_analyzer.invoke import (
    RustAnalyzerInvocationFailed,
    RustAnalyzerNoOutput,
    RustAnalyzerNotInstalled,
)


@pytest.fixture(autouse=True)
def _reset_log_dedup():
    """Clear the one-time log marker so each test starts fresh."""
    _reset_logged_fallback_for_tests()
    yield
    _reset_logged_fallback_for_tests()


def _fake_source_reader(_p: str) -> bytes:  # pragma: no cover — unused path
    return b""


def _make_ok_translate(symbols: List[Symbol], edges: List[Edge]):
    def _translate(_scip: bytes, _reader) -> Tuple[List[Symbol], List[Edge]]:
        return symbols, edges
    return _translate


class TestHappyPath:
    def test_invoke_and_translate_returns_tuple(self, tmp_path: Path) -> None:
        want_sym = Symbol(
            id="rust:src/lib.rs:1-2:foo:function",
            name="foo", kind="function", language="rust",
            path="src/lib.rs", span=Span(1, 2, 0, 0),
        )

        def _invoke(workspace, *, cwd):
            assert workspace == tmp_path
            assert cwd.exists()
            return b"fake-scip-bytes"

        translate = _make_ok_translate([want_sym], [])
        result = try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, translate=translate,
        )
        assert result is not None
        syms, edges = result
        assert syms == [want_sym]
        assert edges == []


class TestInvokeFailureModes:
    def test_not_installed_returns_none(self, tmp_path: Path) -> None:
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerNotInstalled("binary missing")

        log_msgs: list[str] = []
        result = try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke,
            translate=lambda *_a, **_kw: (_ for _ in ()).throw(  # pragma: no cover
                AssertionError("translate should not fire"),
            ),
            log=log_msgs.append,
        )
        assert result is None
        assert len(log_msgs) == 1
        assert "RustAnalyzerNotInstalled" in log_msgs[0]
        assert "falling through to rust.py" in log_msgs[0]

    def test_invocation_failed_returns_none(self, tmp_path: Path) -> None:
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerInvocationFailed("exit 2", b"err")

        log_msgs: list[str] = []
        result = try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert result is None
        assert "RustAnalyzerInvocationFailed" in log_msgs[0]

    def test_no_output_returns_none(self, tmp_path: Path) -> None:
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerNoOutput("cargo metadata failed", b"err")

        log_msgs: list[str] = []
        result = try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert result is None
        assert "RustAnalyzerNoOutput" in log_msgs[0]

    def test_fallback_logged_once_per_workspace(self, tmp_path: Path) -> None:
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerNotInstalled("missing")

        log_msgs: list[str] = []
        for _ in range(3):
            try_analyze_with_rust_analyzer(
                tmp_path, _fake_source_reader,
                invoke=_invoke, log=log_msgs.append,
            )
        assert len(log_msgs) == 1

    def test_fallback_logged_per_distinct_workspace(
        self, tmp_path: Path,
    ) -> None:
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerNotInstalled("missing")

        log_msgs: list[str] = []
        ws_a = tmp_path / "a"
        ws_a.mkdir()
        ws_b = tmp_path / "b"
        ws_b.mkdir()
        try_analyze_with_rust_analyzer(
            ws_a, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        try_analyze_with_rust_analyzer(
            ws_b, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert len(log_msgs) == 2


class TestTranslateFailure:
    def test_decode_error_returns_none(self, tmp_path: Path) -> None:
        def _invoke(workspace, *, cwd):
            return b"truncated-bytes"

        def _translate(_scip, _reader):
            raise DecodeError("bad wire format")

        log_msgs: list[str] = []
        result = try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, translate=_translate, log=log_msgs.append,
        )
        assert result is None
        assert "decode failed" in log_msgs[0]
        assert "bad wire format" in log_msgs[0]

    def test_decode_error_logged_once(self, tmp_path: Path) -> None:
        def _invoke(workspace, *, cwd):
            return b"x"

        def _translate(_scip, _reader):
            raise DecodeError("bad")

        log_msgs: list[str] = []
        for _ in range(3):
            try_analyze_with_rust_analyzer(
                tmp_path, _fake_source_reader,
                invoke=_invoke, translate=_translate, log=log_msgs.append,
            )
        assert len(log_msgs) == 1


class TestDefaultLogIsNoOp:
    def test_default_log_silently_swallows_fallback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When no log callable is passed, the helper stays silent."""
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerNotInstalled("missing")

        result = try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke,  # no log kwarg
        )
        assert result is None
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""
