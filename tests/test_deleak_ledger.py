# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/deleak-ledger``'s ``--map`` clobber guard.

Named to match the script basename (hyphens normalized to underscores) so
``top_level_test_map.py`` selects this file when the script changes — per-PR
smart-test runs it automatically.

Regression: deleak-ledger used to *unconditionally* write
``extract_map(deleaked_notes)`` to the ``--map`` path. A modern
campaign-position-free ledger has no embedded F-numbers, so the reconstruction
is ``{}``; writing that destroyed the orchestrator-maintained
``pass_row_map.json`` — the human's sole summed-severity-over-time source. The
guard now refuses to overwrite a *populated* map with an *empty* reconstruction,
while still writing a genuine reconstruction (the one-time migration aid for an
old cadence-bearing ledger) and still creating the file when absent.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
DELEAK_PATH = REPO_ROOT / "scripts" / "deleak-ledger"


def _load(path: Path, name: str) -> Any:
    """Load a Python source file as a module regardless of extension."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


deleak = _load(DELEAK_PATH, "deleak_ledger")

# A realistic full-form tracker ID (matches the TRACKER_ID regex).
_ID = "WI-abcde-fghij-klmno-parst-uvwxy-zabcd-efghi-jklmn"


def _run(tmp_path: Path, notes: str, existing_map: Any = None):
    """Invoke deleak-ledger's main() over a tmp ledger; return (rc, map_path)."""
    ledger = tmp_path / "agent_notes.json"
    ledger.write_text(json.dumps({"notes": notes}))
    mapf = tmp_path / "pass_row_map.json"
    if existing_map is not None:
        mapf.write_text(json.dumps(existing_map))
    out = tmp_path / "deleaked.json"
    report = tmp_path / "report.md"
    titles = tmp_path / "titles.json"
    titles.write_text("{}")  # empty tracker-titles → no live `tracker` call
    argv = [
        "deleak-ledger",
        "--in", str(ledger),
        "--out", str(out),
        "--map", str(mapf),
        "--report", str(report),
        "--tracker-titles", str(titles),
    ]
    with patch("sys.argv", argv):
        rc = deleak.main()
    return rc, mapf


def test_empty_reconstruction_preserves_populated_map(tmp_path: Path) -> None:
    """A cadence-free ledger must NOT clobber an existing populated map."""
    populated = {"81": [{"f_number": "F81.A1", "severity": 5}]}
    notes = f"# ledger\n- {_ID} — a real issue, no F-number in the row.\n"
    _rc, mapf = _run(tmp_path, notes, existing_map=populated)
    assert json.loads(mapf.read_text()) == populated  # preserved, not {}


def test_reconstruction_written_when_ledger_has_fnumbers(tmp_path: Path) -> None:
    """A cadence-bearing ledger DOES write its reconstructed map (migration aid)."""
    notes = f"# ledger\n- {_ID} — an issue [F81.A1].\n"
    _rc, mapf = _run(tmp_path, notes, existing_map={"stale": []})
    written = json.loads(mapf.read_text())
    assert "81" in written and written["81"][0]["f_number"] == "F81.A1"


def test_empty_reconstruction_creates_map_when_absent(tmp_path: Path) -> None:
    """With no pre-existing map, an empty reconstruction is still created."""
    notes = f"# ledger\n- {_ID} — no F-number.\n"
    _rc, mapf = _run(tmp_path, notes, existing_map=None)
    assert json.loads(mapf.read_text()) == {}  # created (harmless)


# ---------------------------------------------------------------------------
# Discovery-handle cadence forms. The per-pass discovery workers integrate
# findings into the ledger under short LOCAL handles — ``p<localpass>-A<k>``,
# the chunk-qualified ``d<chunk>p<pass>-A<k>``, the bare chunk marker
# ``(d<chunk>)``, and the ``discovery_<chunk>`` staging-dir label. All four
# encode campaign position (which pass/chunk produced a finding) and so are
# cadence the worker-facing ledger must not carry. The token-regex pass missed
# them; these tests pin the delete-only strip + wrapper cleanup.
# ---------------------------------------------------------------------------


def test_strips_pass_finding_handle() -> None:
    """``p<N>-A<k>`` discovery-pass+finding labels are stripped (delete-only)."""
    assert "p2-A2" not in deleak.strip_tokens("[facet p2-A2: tautology check]")
    assert "p1-A3" not in deleak.strip_tokens("- p1-A3: TypeScript zero variable-kind")


def test_strips_chunk_pass_finding_handle() -> None:
    """``d<N>p<N>-A<k>`` chunk+pass+finding labels are stripped."""
    assert "d5p2-A1" not in deleak.strip_tokens("· d5p2-A1 — framework matcher gate")
    assert "d5p1-A1" not in deleak.strip_tokens("[facet d5p1-A1: anchorless files]")


def test_strips_bare_chunk_paren_and_discovery_label() -> None:
    """``(d<N>)`` and ``discovery_<N>`` chunk markers are stripped."""
    s = deleak.strip_tokens("[facet p2-A1(d6): foo] (discovery_7) CORRECTS discovery_4")
    assert "(d6)" not in s
    assert "discovery_7" not in s
    assert "discovery_4" not in s


def test_tidy_collapses_emptied_handle_wrappers() -> None:
    """After a handle is stripped, the empty ``[facet : ]`` / ``· :`` wrapper tidies."""
    assert (
        deleak.tidy(deleak.strip_tokens("[facet p2-A2: tautology check]"))
        == "[facet: tautology check]"
    )
    assert (
        deleak.tidy(deleak.strip_tokens("· p2-A1: edge.meta.channel undocumented"))
        == "· edge.meta.channel undocumented"
    )


def test_new_handle_patterns_preserve_defect_content() -> None:
    """The new patterns must not eat counts / spec refs that are defect content."""
    line = (
        f"- **{_ID}** — 1670 nodes, §815, 0/110533 edges, app2-A1 helper, type-A1 alias"
    )
    out = deleak.tidy(deleak.strip_tokens(line))
    assert "1670 nodes" in out
    assert "§815" in out
    assert "0/110533 edges" in out
    # word-internal look-alikes (no word boundary before ``p\d``) are NOT handles
    assert "app2-A1" in out
    assert "type-A1" in out
