# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the canonical YAML catalog registry."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from hypergumbo_core.yaml_catalogs import (
    YAML_CATALOGS,
    CatalogSpec,
    enumerate_catalogs,
    validate_registry,
)


def test_registry_has_no_duplicate_directories():
    dirs = [spec.directory for spec in YAML_CATALOGS]
    assert len(dirs) == len(set(dirs)), f"duplicate directories: {dirs}"


def test_catalog_spec_is_frozen():
    spec = YAML_CATALOGS[0]
    with pytest.raises(FrozenInstanceError):
        spec.directory = "x"  # type: ignore[misc]


def test_registry_matches_filesystem_clean_tree():
    """The real package tree should have zero drift findings."""
    assert validate_registry() == []


def test_enumerate_catalogs_returns_one_entry_per_spec():
    rows = enumerate_catalogs()
    assert len(rows) == len(YAML_CATALOGS)


def test_enumerate_catalogs_counts_real_files():
    """Every registered catalog should have at least one YAML file shipped."""
    for spec, count in enumerate_catalogs():
        assert count > 0, (
            f"catalog {spec.directory!r} is registered but ships no YAML files"
        )


def _make_fake_tree(root: Path, dirs_with_yaml: dict[str, int]) -> None:
    for name, count in dirs_with_yaml.items():
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            (d / f"{name}_{i}.yaml").write_text("id: x\n")


def test_enumerate_catalogs_with_missing_directory(tmp_path: Path):
    """A registered directory that does not exist is reported with count=0."""
    # tmp_path is empty — none of the registered catalogs exist
    rows = enumerate_catalogs(pkg_root=tmp_path)
    assert all(count == 0 for _, count in rows)


def test_validate_registry_flags_missing_registered_directory(tmp_path: Path):
    findings = validate_registry(pkg_root=tmp_path)
    # Every registered catalog is missing from tmp_path
    assert len(findings) == len(YAML_CATALOGS)
    for spec in YAML_CATALOGS:
        assert any(spec.directory in msg for msg in findings)


def test_validate_registry_flags_unregistered_yaml_directory(tmp_path: Path):
    # Create every registered directory (so those don't flag), plus one extra
    registered_dirs = {s.directory: 1 for s in YAML_CATALOGS}
    registered_dirs["mystery_catalog"] = 3  # not in YAML_CATALOGS
    _make_fake_tree(tmp_path, registered_dirs)

    findings = validate_registry(pkg_root=tmp_path)
    assert len(findings) == 1
    assert "mystery_catalog" in findings[0]
    assert "3 YAML file" in findings[0]


def test_validate_registry_ignores_yamlless_directories(tmp_path: Path):
    # Create every registered directory + one empty (no YAML) extra
    registered_dirs = {s.directory: 1 for s in YAML_CATALOGS}
    _make_fake_tree(tmp_path, registered_dirs)
    (tmp_path / "empty_dir").mkdir()

    findings = validate_registry(pkg_root=tmp_path)
    assert findings == []


def test_validate_registry_ignores_top_level_files(tmp_path: Path):
    """A loose .yaml file at package root is not a catalog."""
    registered_dirs = {s.directory: 1 for s in YAML_CATALOGS}
    _make_fake_tree(tmp_path, registered_dirs)
    (tmp_path / "loose.yaml").write_text("x: 1\n")

    findings = validate_registry(pkg_root=tmp_path)
    assert findings == []


def test_catalog_spec_fields_populated():
    """Every spec carries the four required descriptive fields."""
    for spec in YAML_CATALOGS:
        assert spec.directory and isinstance(spec.directory, str)
        assert spec.purpose and isinstance(spec.purpose, str)
        assert spec.loader and isinstance(spec.loader, str)
        # adr is Optional but in practice every current catalog has one
        assert spec.adr is None or spec.adr.startswith("ADR-")


def test_catalog_spec_constructor_accepts_none_adr():
    """The adr field is Optional — verify None is accepted."""
    spec = CatalogSpec(
        directory="x", purpose="y", loader="z", adr=None
    )
    assert spec.adr is None
