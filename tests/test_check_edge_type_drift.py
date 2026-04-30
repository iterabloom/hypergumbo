# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/check-edge-type-drift``.

Named to match the script basename (with hyphens normalized to
underscores) so ``top_level_test_map.py`` selects this file when the
script changes — per-PR smart-test runs these tests automatically.

The drift-detection logic lives in
``hypergumbo_core.edge_types.find_axis_drift`` and is exercised
exhaustively by ``packages/hypergumbo-core/tests/test_edge_types.py``;
this file covers only the thin CLI shell.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-edge-type-drift"


def _load(path: Path, name: str) -> Any:
    """Load a Python source file as a module regardless of extension."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


cli = _load(SCRIPT_PATH, "check_edge_type_drift")


def test_main_returns_zero_when_no_drift(capsys):
    with patch.object(cli, "find_axis_drift", return_value=[]):
        rc = cli.main()
    assert rc == 0
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == ""


def test_main_returns_one_and_prints_offenders_when_drift_detected(capsys):
    offenders = [
        "packages/foo/bar.py:42 (_X_EDGE_TYPES): "
        "contains ['phantom-value'] not in canonical registry",
    ]
    with patch.object(cli, "find_axis_drift", return_value=offenders):
        rc = cli.main()
    assert rc == 1
    captured = capsys.readouterr()
    assert "axis-coherence check failed" in captured.out
    assert "phantom-value" in captured.out
    # Fix instructions go to stderr.
    assert "edge_types.py" in captured.err
    assert "ADR-0023" in captured.err
