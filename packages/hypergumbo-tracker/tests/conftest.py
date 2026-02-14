# SPDX-License-Identifier: MPL-2.0
"""Shared test fixtures for hypergumbo-tracker tests.

Provides common fixtures for temporary directories, sample configs,
and op construction helpers used across test modules.
"""

from __future__ import annotations

import os
import textwrap
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
    cfg = tmp_path / "config.yaml"
    cfg.write_text(textwrap.dedent("""\
        kinds:
          invariant:
            prefix: INV
            description: "A violated invariant"
            fields_schema:
              statement:
                type: text
                required: true
              root_cause:
                type: text
                required: true
              fix:
                type: text
              verification:
                type: text
              regression_tests:
                type: list
              scope:
                type: text
              progress_pct:
                type: integer
                min: 0
                max: 100
          meta_invariant:
            prefix: META
            description: "A meta-invariant tracking cross-language coverage"
            fields_schema:
              statement:
                type: text
                required: true
              languages_done:
                type: list
              languages_remaining:
                type: list
              progress_pct:
                type: integer
                min: 0
                max: 100
          work_item:
            prefix: WI
            description: "A work item"
        statuses:
          - todo_hard
          - todo_soft
          - in_progress
          - done
          - deferred
          - wont_do
        stop_hook:
          blocking_statuses:
            - todo_hard
            - todo_soft
          resolved_statuses:
            - done
            - deferred
            - wont_do
        well_known_tags:
          - developer_experience
          - cross_language_linkers
          - analysis_quality
        actor_resolution:
          agent_usernames:
            - "*_agent"
        lamport_branches:
          - dev
          - main
    """))
    return cfg


@pytest.fixture()
def sample_create_data() -> dict[str, Any]:
    """Return sample data dict for a create op."""
    return {
        "kind": "invariant",
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
    import struct

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
