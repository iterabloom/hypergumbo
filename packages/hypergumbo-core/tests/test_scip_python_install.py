# SPDX-License-Identifier: AGPL-3.0-or-later
"""Availability probes for the scip-python backend (WI-nanom).

Mirrors ``rust_analyzer_install``: the binary probe is ``which`` plus a
``--version`` smoke test through the ``repo_inspection`` zone wrapper, and
the integration probe is an ``importlib`` lookup. Both take injectable
callables so no test touches ``PATH`` or shells out.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

from hypergumbo_core.scip_python_install import (
    SCIP_PYTHON_BINARY,
    SCIP_PYTHON_NPM_PACKAGE,
    SCIP_PYTHON_PINNED_VERSION,
    is_scip_python_available,
    is_scip_python_integration_installed,
)


@dataclass
class _Completed:
    returncode: int = 0
    stderr: bytes = b""
    stdout: bytes = b""


def _found(name: str) -> Optional[str]:
    return f"/fake/bin/{name}"


class TestTheBinaryProbe:
    def test_the_constants_name_the_pinned_npm_package(self) -> None:
        assert SCIP_PYTHON_BINARY == "scip-python"
        assert SCIP_PYTHON_NPM_PACKAGE == "@sourcegraph/scip-python"
        assert SCIP_PYTHON_PINNED_VERSION == "0.6.6"

    def test_missing_binary_is_unavailable_without_running_anything(self) -> None:
        calls: list[list[str]] = []
        assert is_scip_python_available(which=lambda n: None, runner=lambda argv, **kw: calls.append(argv)) is False
        assert calls == []

    def test_a_working_binary_is_available(self) -> None:
        seen: list[list[str]] = []

        def runner(argv, **kwargs):
            seen.append(argv)
            return _Completed(returncode=0, stdout=b"0.6.6\n")

        assert is_scip_python_available(which=_found, runner=runner) is True
        assert seen == [["/fake/bin/scip-python", "--version"]]

    def test_a_broken_binary_is_unavailable(self) -> None:
        assert is_scip_python_available(which=_found, runner=lambda argv, **kw: _Completed(returncode=1)) is False

    def test_a_timeout_or_oserror_is_unavailable(self) -> None:
        def timeout(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 5.0)

        def oserror(argv, **kw):
            raise OSError("boom")

        assert is_scip_python_available(which=_found, runner=timeout) is False
        assert is_scip_python_available(which=_found, runner=oserror) is False


class TestTheIntegrationProbe:
    def test_present_and_absent(self) -> None:
        assert is_scip_python_integration_installed(find_spec=lambda name: object()) is True
        assert is_scip_python_integration_installed(find_spec=lambda name: None) is False

    def test_the_default_asks_importlib_for_the_package(self) -> None:
        asked: list[str] = []

        def find_spec(name: str):
            asked.append(name)
            return None

        is_scip_python_integration_installed(find_spec=find_spec)
        assert asked == ["hypergumbo_lang_scip_python"]
