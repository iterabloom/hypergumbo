# SPDX-License-Identifier: AGPL-3.0-or-later
"""PowerShell call routes stamp what scoped their choice (WI-hirol).

``Edge.meta["resolved_by"]`` (:mod:`hypergumbo_core.resolved_by`): every
resolved command is the resolver's own value, ``name`` for a unique function,
in this file or another.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/powershell.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.powershell import analyze_powershell


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).write_text(text)
    return root


def _mark(edges: list[Edge], line: int, dst_name: str) -> str:
    hits = [
        e for e in edges
        if e.edge_type == "calls" and e.is_resolved and e.line == line
        and e.dst.split(":")[-2] == dst_name
    ]
    assert len(hits) == 1, [(e.line, e.dst) for e in edges]
    return hits[0].meta["resolved_by"]


def test_powershell(tmp_path: Path) -> None:
    edges = analyze_powershell(_write(tmp_path, {
        "lib.ps1": "function Get-Far {\n  Write-Output 1\n}\n",
        "main.ps1": (
            "function Get-Local {\n  Write-Output 2\n}\n"
            "function Invoke-Run {\n  Get-Local\n  Get-Far\n}\n"
        ),
    })).edges
    assert _mark(edges, 5, "Get-Local") == "name"
    assert _mark(edges, 6, "Get-Far") == "name"
