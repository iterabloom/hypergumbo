# SPDX-License-Identifier: AGPL-3.0-or-later
"""The legacy arm of the Go import-versioning A/B leaves the analyzer as found.

``run_arm(legacy=True)`` rebinds ``go._go_package_identifier`` to the legacy
rule so one process can measure the old behaviour. The rebinding is for that
call only: a caller in the same process (another test, or the default arm run
after it) must get production's identifier back, including when the analysis
raises (INV-nuvib).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "measure-go-import-versioning.py"


def _load() -> ModuleType:
    """Load the shipped script; its hyphenated name is not importable."""
    spec = importlib.util.spec_from_loader(
        "measure_go_import_versioning",
        importlib.machinery.SourceFileLoader(
            "measure_go_import_versioning", str(SCRIPT)),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(monkeypatch: pytest.MonkeyPatch, *, raises: bool):
    from hypergumbo_lang_mainstream import go as go_mod

    script = _load()
    during: dict[str, object] = {}

    def fake_analyze(repo):
        during["identifier"] = go_mod._go_package_identifier
        if raises:
            raise RuntimeError("analysis failed")
        return SimpleNamespace(edges=[], symbols=[])

    monkeypatch.setattr(go_mod, "analyze_go", fake_analyze)
    before = go_mod._go_package_identifier
    if raises:
        with pytest.raises(RuntimeError):
            script.run_arm(Path("/repo"), legacy=True)
    else:
        script.run_arm(Path("/repo"), legacy=True)
    return script, before, during, go_mod._go_package_identifier


def test_the_legacy_arm_runs_with_the_legacy_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reach: the arm still measures what it exists to measure."""
    script, _, during, _ = _run(monkeypatch, raises=False)
    assert during["identifier"] is script._legacy_identifier


def test_the_identifier_is_restored_on_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, before, _, after = _run(monkeypatch, raises=False)
    assert after is before


def test_the_identifier_is_restored_when_the_analysis_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, before, _, after = _run(monkeypatch, raises=True)
    assert after is before
