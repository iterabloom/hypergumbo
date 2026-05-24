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


class TestRichDiagnosticsForInvocationFailed:
    """WI-todon: surface exit code + stderr tail + OOM hint when RA crashes."""

    def test_log_includes_exit_code(self, tmp_path: Path) -> None:
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerInvocationFailed(
                "rust-analyzer scip exited 2", b"thread panicked: foo",
                returncode=2,
            )

        log_msgs: list[str] = []
        try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert len(log_msgs) == 1
        assert "exit=2" in log_msgs[0]

    def test_log_includes_stderr_tail(self, tmp_path: Path) -> None:
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerInvocationFailed(
                "rust-analyzer scip exited 2",
                b"error: cargo metadata failed: package foo not found",
                returncode=2,
            )

        log_msgs: list[str] = []
        try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert "cargo metadata failed: package foo not found" in log_msgs[0]

    def test_returncode_negative_nine_emits_oom_hint(self, tmp_path: Path) -> None:
        """SIGKILL on Linux surfaces as -9 → loudly call out memory exhaustion."""
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerInvocationFailed(
                "rust-analyzer scip exited -9", b"", returncode=-9,
            )

        log_msgs: list[str] = []
        try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert "OOM" in log_msgs[0] or "memory" in log_msgs[0].lower()
        assert "SIGKILL" in log_msgs[0]

    def test_returncode_137_emits_oom_hint(self, tmp_path: Path) -> None:
        """Shell-convention 128+9 also indicates SIGKILL."""
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerInvocationFailed(
                "rust-analyzer scip exited 137", b"", returncode=137,
            )

        log_msgs: list[str] = []
        try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert "OOM" in log_msgs[0] or "memory" in log_msgs[0].lower()
        assert "SIGKILL" in log_msgs[0]

    def test_returncode_2_does_not_emit_oom_hint(self, tmp_path: Path) -> None:
        """Exit 2 is a regular cargo/parse error, NOT SIGKILL — no OOM hint."""
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerInvocationFailed(
                "rust-analyzer scip exited 2", b"cargo error", returncode=2,
            )

        log_msgs: list[str] = []
        try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert "OOM" not in log_msgs[0]
        assert "SIGKILL" not in log_msgs[0]

    def test_timeout_has_no_returncode_no_oom_hint(self, tmp_path: Path) -> None:
        """Timeout sets returncode=None — log omits exit and OOM."""
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerInvocationFailed(
                "rust-analyzer scip timed out after 600.0s",
                b"", returncode=None,
            )

        log_msgs: list[str] = []
        try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert "exit=" not in log_msgs[0]
        assert "OOM" not in log_msgs[0]
        assert "timed out" in log_msgs[0]

    def test_long_stderr_truncated_to_tail(self, tmp_path: Path) -> None:
        """Long stderr is truncated to a fixed tail to keep logs scannable."""
        long_stderr = (b"noise\n" * 200) + b"FINAL_LINE_OF_INTEREST"

        def _invoke(workspace, *, cwd):
            raise RustAnalyzerInvocationFailed(
                "rust-analyzer scip exited 2", long_stderr, returncode=2,
            )

        log_msgs: list[str] = []
        try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert "FINAL_LINE_OF_INTEREST" in log_msgs[0]
        assert len(log_msgs[0]) < len(long_stderr)

    def test_empty_stderr_omits_stderr_section(self, tmp_path: Path) -> None:
        """Empty stderr (e.g. SIGKILL'd before any output) → no 'stderr:' chunk."""
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerInvocationFailed(
                "rust-analyzer scip exited -9", b"", returncode=-9,
            )

        log_msgs: list[str] = []
        try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert "stderr:" not in log_msgs[0]


class TestRichDiagnosticsForNoOutput:
    """WI-todon: RustAnalyzerNoOutput's stderr (cargo metadata) reaches the user."""

    def test_log_includes_stderr_tail(self, tmp_path: Path) -> None:
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerNoOutput(
                "no index.scip",
                b"error: failed to parse Cargo.toml: invalid syntax",
            )

        log_msgs: list[str] = []
        try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert "failed to parse Cargo.toml" in log_msgs[0]

    def test_empty_stderr_omits_stderr_section(self, tmp_path: Path) -> None:
        def _invoke(workspace, *, cwd):
            raise RustAnalyzerNoOutput("no index.scip", b"")

        log_msgs: list[str] = []
        try_analyze_with_rust_analyzer(
            tmp_path, _fake_source_reader,
            invoke=_invoke, log=log_msgs.append,
        )
        assert "stderr:" not in log_msgs[0]
