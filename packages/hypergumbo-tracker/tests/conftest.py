# SPDX-License-Identifier: MPL-2.0
"""Shared test fixtures for hypergumbo-tracker tests.

Provides common fixtures for temporary directories, sample configs,
and op construction helpers used across test modules.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture()
def ops_dir(tmp_path: Path) -> Path:
    """Create a temporary .ops directory for store tests."""
    d = tmp_path / ".ops"
    d.mkdir()
    return d


@pytest.fixture()
def config_yaml(tmp_path: Path) -> Path:
    """Write a minimal tracker config.yaml and return its path."""
    import yaml

    from helpers import make_test_config_dict

    cfg = tmp_path / "config.yaml"
    config_dict = make_test_config_dict(
        well_known_tags=[
            "developer_experience",
            "cross_language_linkers",
            "analysis_quality",
        ],
    )
    cfg.write_text(yaml.dump(config_dict))
    return cfg


@pytest.fixture()
def sample_create_data() -> dict[str, Any]:
    """Return sample data dict for a create op."""
    return {
        "kind": "work_item",
        "title": "Symbol IDs must be stable across runs",
        "status": "todo_hard",
        "priority": 1,
        "tags": ["analysis_quality"],
        "description": "Symbol IDs change between runs causing flaky diffs.",
        "fields": {
            "statement": "Symbol IDs must be deterministic given the same input.",
            "root_cause": "Hash includes timestamp.",
        },
    }


@pytest.fixture()
def mock_agent_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch os.getuid to return a UID whose username ends in _agent."""
    import pwd

    # Use a UID that maps to a fake passwd entry
    fake_uid = 60000

    class FakePwEntry:
        pw_name = "test_agent"
        pw_uid = fake_uid
        pw_gid = fake_uid
        pw_gecos = ""
        pw_dir = "/tmp"
        pw_shell = "/bin/false"

    monkeypatch.setattr(os, "getuid", lambda: fake_uid)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: FakePwEntry())


@pytest.fixture()
def mock_human_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch os.getuid to return a UID whose username does NOT end in _agent."""
    import pwd

    fake_uid = 60001

    class FakePwEntry:
        pw_name = "jgstern"
        pw_uid = fake_uid
        pw_gid = fake_uid
        pw_gecos = ""
        pw_dir = "/home/jgstern"
        pw_shell = "/bin/bash"

    monkeypatch.setattr(os, "getuid", lambda: fake_uid)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: FakePwEntry())
