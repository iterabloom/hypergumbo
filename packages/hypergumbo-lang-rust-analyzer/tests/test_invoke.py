# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for :mod:`hypergumbo_lang_rust_analyzer.invoke` (WI-duzul Slice B).

The module under test shells out to ``rust-analyzer scip`` — these
tests do **not** invoke the real binary. Every path is exercised
through the ``which`` and ``runner`` injection points so the test
suite is fast, hermetic, and portable. The "real binary" path is an
integration concern deferred to a separate gated test once the
analyzer-registry wiring lands.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from hypergumbo_lang_rust_analyzer.invoke import (
    RustAnalyzerInvocationFailed,
    RustAnalyzerNoOutput,
    RustAnalyzerNotInstalled,
    run_rust_analyzer_scip,
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


class TestResolveBinary:
    def test_missing_binary_raises_not_installed(self, tmp_path: Path) -> None:
        with pytest.raises(RustAnalyzerNotInstalled, match="rust-analyzer"):
            run_rust_analyzer_scip(
                tmp_path, cwd=tmp_path, which=_which_missing,
            )

    def test_error_message_includes_requested_name(
        self, tmp_path: Path,
    ) -> None:
        with pytest.raises(RustAnalyzerNotInstalled, match="custom-bin"):
            run_rust_analyzer_scip(
                tmp_path, cwd=tmp_path,
                rust_analyzer_bin="custom-bin",
                which=_which_missing,
            )


class TestRunnerSuccess:
    def test_returns_scip_bytes_when_index_written(
        self, tmp_path: Path,
    ) -> None:
        scip_payload = b"\x00\x01fake-scip"
        (tmp_path / "index.scip").write_bytes(scip_payload)

        def _runner(cmd, *, cwd, capture_output, timeout):
            assert Path(cwd) == tmp_path
            assert cmd[1] == "scip"
            assert capture_output is True
            assert timeout == 600.0
            return _Completed()

        out = run_rust_analyzer_scip(
            tmp_path, cwd=tmp_path,
            which=_which_found, runner=_runner,
        )
        assert out == scip_payload

    def test_passes_workspace_as_third_arg(self, tmp_path: Path) -> None:
        workspace = tmp_path / "some" / "crate"
        workspace.mkdir(parents=True)
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        (scratch / "index.scip").write_bytes(b"x")

        seen_cmd: list[str] = []

        def _runner(cmd, *, cwd, capture_output, timeout):
            seen_cmd.extend(cmd)
            return _Completed()

        run_rust_analyzer_scip(
            workspace, cwd=scratch,
            which=_which_found, runner=_runner,
        )
        assert seen_cmd[0] == "/fake/bin/rust-analyzer"
        assert seen_cmd[1] == "scip"
        assert seen_cmd[2] == str(workspace)

    def test_custom_timeout_forwarded(self, tmp_path: Path) -> None:
        (tmp_path / "index.scip").write_bytes(b"x")
        seen: dict[str, float] = {}

        def _runner(cmd, *, cwd, capture_output, timeout):
            seen["timeout"] = timeout
            return _Completed()

        run_rust_analyzer_scip(
            tmp_path, cwd=tmp_path,
            which=_which_found, runner=_runner, timeout_sec=42.5,
        )
        assert seen["timeout"] == 42.5


class TestRunnerFailures:
    def test_nonzero_exit_raises_invocation_failed(
        self, tmp_path: Path,
    ) -> None:
        def _runner(cmd, *, cwd, capture_output, timeout):
            return _Completed(returncode=2, stderr=b"boom")

        with pytest.raises(RustAnalyzerInvocationFailed) as excinfo:
            run_rust_analyzer_scip(
                tmp_path, cwd=tmp_path,
                which=_which_found, runner=_runner,
            )
        assert "exited 2" in str(excinfo.value)
        assert excinfo.value.stderr == b"boom"

    def test_timeout_raises_invocation_failed(self, tmp_path: Path) -> None:
        def _runner(cmd, *, cwd, capture_output, timeout):
            raise subprocess.TimeoutExpired(
                cmd=cmd, timeout=timeout, stderr=b"stuck",
            )

        with pytest.raises(RustAnalyzerInvocationFailed) as excinfo:
            run_rust_analyzer_scip(
                tmp_path, cwd=tmp_path,
                which=_which_found, runner=_runner, timeout_sec=5.0,
            )
        assert "timed out after 5.0s" in str(excinfo.value)
        assert excinfo.value.stderr == b"stuck"

    def test_timeout_with_no_captured_stderr(self, tmp_path: Path) -> None:
        """TimeoutExpired may not carry stderr — exception still raises cleanly."""
        def _runner(cmd, *, cwd, capture_output, timeout):
            raise subprocess.TimeoutExpired(
                cmd=cmd, timeout=timeout,
            )

        with pytest.raises(RustAnalyzerInvocationFailed) as excinfo:
            run_rust_analyzer_scip(
                tmp_path, cwd=tmp_path,
                which=_which_found, runner=_runner,
            )
        assert excinfo.value.stderr == b""

    def test_success_exit_but_no_index_raises_no_output(
        self, tmp_path: Path,
    ) -> None:
        def _runner(cmd, *, cwd, capture_output, timeout):
            return _Completed(stderr=b"metadata failed")

        with pytest.raises(RustAnalyzerNoOutput) as excinfo:
            run_rust_analyzer_scip(
                tmp_path, cwd=tmp_path,
                which=_which_found, runner=_runner,
            )
        assert "no index.scip" in str(excinfo.value)
        assert excinfo.value.stderr == b"metadata failed"

    def test_completed_without_stderr_handled(self, tmp_path: Path) -> None:
        """runner may return a CompletedProcess with stderr=None (defensive)."""
        def _runner(cmd, *, cwd, capture_output, timeout):
            return _Completed(returncode=1, stderr=None)  # type: ignore[arg-type]

        with pytest.raises(RustAnalyzerInvocationFailed) as excinfo:
            run_rust_analyzer_scip(
                tmp_path, cwd=tmp_path,
                which=_which_found, runner=_runner,
            )
        assert excinfo.value.stderr == b""

    def test_no_output_without_stderr_handled(self, tmp_path: Path) -> None:
        def _runner(cmd, *, cwd, capture_output, timeout):
            return _Completed(stderr=None)  # type: ignore[arg-type]

        with pytest.raises(RustAnalyzerNoOutput) as excinfo:
            run_rust_analyzer_scip(
                tmp_path, cwd=tmp_path,
                which=_which_found, runner=_runner,
            )
        assert excinfo.value.stderr == b""
