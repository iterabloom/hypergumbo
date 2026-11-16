# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests that ``resolve_workdir`` prefix-isolates its session scan.

Both ``scripts/bakeoff-broad`` and ``scripts/bakeoff-deep`` share one
artifacts directory (``~/hypergumbo_lab_notebook/bakeoff_artifacts``), so
``broad-*`` and ``deep-*`` session directories intermingle. Sorting the full
directory names lexicographically would be wrong: because ``d`` > ``b`` in
ASCII, any ``deep-*`` directory out-sorts any ``broad-*`` directory regardless
of the timestamp suffix, so ``bakeoff-broad`` would pick up a stale deep
session and ``bakeoff-deep`` would pick up a broad session that doesn't
belong to it.

Each script's ``resolve_workdir`` therefore filters auto-discovered sessions
to its own mode prefix (``broad-`` / ``deep-``) before taking the most recent,
by delegating to the ``_find_latest_session`` helper. These tests call
``resolve_workdir`` directly so they don't depend on subprocess/stdout
scraping. (INV-fogat)
"""

# covers: scripts/bakeoff-broad, scripts/bakeoff-deep

import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_script(filename: str, module_name: str):
    """Import an extension-less ``scripts/`` file as a module."""
    loader = importlib.machinery.SourceFileLoader(
        module_name, str(SCRIPTS / filename)
    )
    spec = importlib.util.spec_from_loader(module_name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def broad():
    return _load_script("bakeoff-broad", "bakeoff_broad_under_test")


@pytest.fixture(scope="module")
def deep():
    return _load_script("bakeoff-deep", "bakeoff_deep_under_test")


def _make_session(base: Path, name: str) -> Path:
    """Create a minimal session dir with a state.json under *base*."""
    sess = base / name
    sess.mkdir(parents=True, exist_ok=True)
    (sess / "state.json").write_text(json.dumps({"session_id": name}))
    return sess


# --- bakeoff-broad: must ignore deep-* sessions ----------------------------


def test_broad_prefers_own_prefix_over_newer_deep(broad, tmp_path):
    """A stale broad-* must win over a lexically-larger, newer deep-*."""
    _make_session(tmp_path, "deep-20260601-000000")  # newer, lexically larger
    broad_sess = _make_session(tmp_path, "broad-20260510-000000")  # older
    resolved = broad.resolve_workdir(str(tmp_path))
    assert resolved == str(broad_sess)


def test_broad_ignores_deep_only_dir(broad, tmp_path):
    """With only deep-* sessions present, broad auto-discover finds none."""
    _make_session(tmp_path, "deep-20260601-000000")
    resolved = broad.resolve_workdir(str(tmp_path))
    # Falls through to the base workdir (no broad session to operate on).
    assert resolved == str(tmp_path)


def test_broad_picks_latest_of_own_prefix(broad, tmp_path):
    """Among broad-* sessions the most recent timestamp wins."""
    _make_session(tmp_path, "broad-20260510-000000")
    newest = _make_session(tmp_path, "broad-20260531-022124")
    resolved = broad.resolve_workdir(str(tmp_path))
    assert resolved == str(newest)


# --- bakeoff-deep: must ignore broad-* sessions ----------------------------


def test_deep_ignores_newer_broad(deep, tmp_path):
    """bakeoff-deep must not auto-discover a broad-* session even if newer."""
    _make_session(tmp_path, "broad-20260601-000000")  # newer
    resolved = deep.resolve_workdir(str(tmp_path))
    # No deep-* session exists; must fall through to base, never the broad one.
    assert resolved == str(tmp_path)


def test_deep_prefers_own_prefix(deep, tmp_path):
    """A deep-* session is selected over a co-resident broad-* session."""
    _make_session(tmp_path, "broad-20260601-000000")
    deep_sess = _make_session(tmp_path, "deep-20260510-054430")
    resolved = deep.resolve_workdir(str(tmp_path))
    assert resolved == str(deep_sess)
