# SPDX-License-Identifier: AGPL-3.0-or-later
"""One sentence-transformers requirement, and installed copies are held to it.

PYSEC-2026-4164 (local model loading bypasses ``trust_remote_code`` and runs
custom Python) is fixed in sentence-transformers 5.6.0. The requirement was
``~=5.2.2``, which admits 5.2.x only, so ``pip install hypergumbo[embeddings]``
could not reach the fix. The string lived in five places (both ``embeddings``
extras, ``install-embeddings``, ``scripts/install-embeddings`` and the I/O
allowlist), and ``install-embeddings`` returned "already installed" for any
importable version, so a user on 5.2.2 was never moved off it.
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hypergumbo_core import cli
from hypergumbo_core.cli import EMBEDDINGS_REQUIREMENT, cmd_install_embeddings

REPO_ROOT = Path(__file__).resolve().parents[3]


def _extra(pyproject: str) -> list[str]:
    """The ``embeddings`` extra's entries (no tomllib on Python 3.10)."""
    block = re.search(
        r"^embeddings = \[(.*?)\]", (REPO_ROOT / pyproject).read_text(), re.M | re.S,
    )
    assert block, f"{pyproject} declares no embeddings extra"
    return re.findall(r'"([^"]+)"', block.group(1))


@pytest.mark.parametrize("pyproject", [
    "packages/hypergumbo-core/pyproject.toml",
    "packages/hypergumbo/pyproject.toml",
])
def test_embeddings_extra_is_the_one_requirement(pyproject: str) -> None:
    assert _extra(pyproject) == [EMBEDDINGS_REQUIREMENT]


@pytest.mark.parametrize("path", [
    "scripts/install-embeddings",
    "docs/hypergumbo.io-allowlist.yaml",
])
def test_other_copies_name_the_one_requirement(path: str) -> None:
    text = (REPO_ROOT / path).read_text()
    assert EMBEDDINGS_REQUIREMENT in text
    assert "sentence-transformers~=5.2" not in text


@pytest.mark.parametrize(("version", "ok"), [
    ("5.7.0", True), ("5.7.1", True), ("5.10.0", True), ("5.7.0rc1", True),
    ("5.6.1", False), ("5.2.2", False), ("6.0.0", False), ("unknown", False),
])
def test_version_check(version: str, ok: bool) -> None:
    assert cli._embeddings_version_ok(version) is ok


def _args(**kw: bool) -> argparse.Namespace:
    return argparse.Namespace(check=kw.get("check", False), quiet=kw.get("quiet", True))


def test_an_outdated_install_is_upgraded() -> None:
    ok = MagicMock(returncode=0)
    with patch.object(cli, "_is_embeddings_available", return_value=True), \
            patch.object(cli, "_get_embeddings_version", return_value="5.2.2"), \
            patch.object(cli, "_embedding_models_cached", return_value=True), \
            patch.object(subprocess, "run", return_value=ok) as run:
        assert cmd_install_embeddings(_args()) == 0
    cmd = run.call_args.args[0]
    assert cmd[1:4] == ["-m", "pip", "install"]
    assert EMBEDDINGS_REQUIREMENT in cmd


def test_a_current_install_is_left_alone() -> None:
    with patch.object(cli, "_is_embeddings_available", return_value=True), \
            patch.object(cli, "_get_embeddings_version", return_value="5.7.0"), \
            patch.object(cli, "_embedding_models_cached", return_value=True), \
            patch.object(subprocess, "run") as run:
        assert cmd_install_embeddings(_args()) == 0
    run.assert_not_called()


def test_check_reports_an_outdated_install(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(cli, "_is_embeddings_available", return_value=True), \
            patch.object(cli, "_get_embeddings_version", return_value="5.2.2"):
        assert cmd_install_embeddings(_args(check=True)) == 1
    out = capsys.readouterr().out
    assert "5.2.2" in out and EMBEDDINGS_REQUIREMENT in out


def test_an_outdated_install_says_it_is_upgrading(capsys: pytest.CaptureFixture[str]) -> None:
    ok = MagicMock(returncode=0)
    with patch.object(cli, "_is_embeddings_available", return_value=True), \
            patch.object(cli, "_get_embeddings_version", return_value="5.2.2"), \
            patch.object(cli, "_embedding_models_cached", return_value=True), \
            patch.object(subprocess, "run", return_value=ok):
        assert cmd_install_embeddings(_args(quiet=False)) == 0
    assert "5.2.2 is older than the required" in capsys.readouterr().out
