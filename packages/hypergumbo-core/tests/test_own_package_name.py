# SPDX-License-Identifier: AGPL-3.0-or-later
"""``supply_chain.own_package_name``: one manifest's own published name (WI-rilof)."""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.supply_chain import own_package_name


def test_reads_each_supported_manifest(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "crate-b"\n')
    (tmp_path / "package.json").write_text('{"name": "@ws/b"}')
    (tmp_path / "go.mod").write_text("module example.com/b\n")
    assert own_package_name(tmp_path / "Cargo.toml") == "crate-b"
    assert own_package_name(tmp_path / "package.json") == "@ws/b"
    assert own_package_name(tmp_path / "go.mod") == "example.com/b"


def test_a_virtual_workspace_manifest_names_no_package(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["a"]\n')
    assert own_package_name(tmp_path / "Cargo.toml") is None


def test_other_basenames_and_missing_files_are_none(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "p"\n')
    assert own_package_name(tmp_path / "pyproject.toml") is None
    assert own_package_name(tmp_path / "missing" / "package.json") is None
