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


@pytest.fixture(autouse=True)
def _isolate_ops_journal(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the out-of-repo ops journal to a per-test tmp dir.

    Tracker mutations mirror each op to a journal outside the repo (durability
    substrate, ``journal.py``). Several tests create real git repos (test_sync,
    test_journal), so without this an op would be mirrored to the *real* default
    journal under ``~/hypergumbo_lab_notebook``. Tests that need a specific
    journal location override this via their own ``monkeypatch.setenv`` (which
    runs after autouse setup).
    """
    from hypergumbo_tracker import journal

    monkeypatch.setenv(
        journal.JOURNAL_ROOT_ENV, str(tmp_path_factory.mktemp("ops-journal"))
    )


@pytest.fixture(autouse=True)
def _isolate_protected_config_root(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the host-protected config root to a tmp path that does not exist.

    THE HOST DECIDED THE TEST, WHICH IS THE DEFECT. ``protected_config`` treats
    the EXISTENCE of ``/etc/hypergumbo-tracker/`` as the opt-in signal, and
    refuses to fall back when the directory exists but holds no entry for the
    repository being loaded -- deliberately, because a silent fallback is the
    downgrade attack the module was written to close. Tests that build a repo
    under ``tmp_path`` therefore inherit whatever the DEVELOPER'S MACHINE has:
    on a host that never opted in they pass, and on a host running the
    two-account setup all thirteen raise ``ProtectedConfigError`` from
    ``load_config``. Same tree, same commit, two answers.

    Observed as 13 failures on a full-suite run on an opted-in host
    (test_sync's whole ``TestPreflightCheck``, test_configure, test_setup);
    CI never saw them because its containers have no ``/etc/hypergumbo-tracker``.
    That asymmetry is the reason this is a fixture and not thirteen edits: the
    next test to build a repo under ``tmp_path`` would have inherited the same
    host dependence.

    MODELLING THE CONDITION RATHER THAN DETECTING IT. The fixture does not ask
    whether the real root exists -- a skip-if-present guard would leave the
    tests untested on exactly the hosts that run the configuration they are
    about. It pins the root to a path guaranteed ABSENT, so every test starts
    from "protection not opted in" on every host, and a test that wants
    protection ON opts in explicitly. There is deliberately no environment
    override to use here (the agent controls its own environment, so an
    env-redirectable root would reopen the hole), which is why this patches the
    module attribute, exactly as ``test_protected_config`` already does.

    Tests needing a populated root override this with their own
    ``monkeypatch.setattr(pc, "PROTECTED_ROOT", ...)``, which runs after autouse
    setup -- the same ordering ``_isolate_ops_journal`` relies on.
    """
    from hypergumbo_tracker import protected_config

    absent = tmp_path_factory.mktemp("protected-config") / "not-opted-in"
    monkeypatch.setattr(protected_config, "PROTECTED_ROOT", absent)


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
