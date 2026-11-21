# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: scripts/check-symbol-kind-drift
"""Tests for ``scripts/check-symbol-kind-drift``.

The script runs two drift checks in the one pre-commit step: Symbol.kind's
``*KIND*`` set walker and, since ADR-0063, the entrypoint-kind axis's
hand-written-set check (``entrypoint_kinds.find_kind_set_drift``). The checks
themselves are tested in ``packages/hypergumbo-core/tests/test_symbol_kinds.py``
and ``test_entrypoint_kinds.py``; this file covers the shell: which exit code
each combination gives and what it prints.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-symbol-kind-drift"


def _load(path: Path, name: str) -> Any:
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cli = _load(SCRIPT_PATH, "check_symbol_kind_drift")


@pytest.mark.parametrize(("kind_drift", "entry_drift", "rc"), [
    ([], [], 0),
    (["a.py:1 (X_KINDS): phantom"], [], 1),
    ([], ["b.py:2: hand-written kind set"], 1),
    (["a.py:1 (X_KINDS): phantom"], ["b.py:2: hand-written kind set"], 1),
])
def test_exit_code_reflects_either_check(
    capsys: pytest.CaptureFixture[str], kind_drift: list[str],
    entry_drift: list[str], rc: int,
) -> None:
    with patch.object(cli, "find_axis_drift", return_value=kind_drift), \
            patch.object(cli, "find_kind_set_drift", return_value=entry_drift):
        assert cli.main() == rc
    out = capsys.readouterr()
    assert ("entrypoint-kind axis check failed" in out.out) == bool(entry_drift)
    assert ("kinds_with_role" in out.err) == bool(entry_drift)
    assert ("Symbol.kind axis-coherence check failed" in out.out) == bool(kind_drift)
