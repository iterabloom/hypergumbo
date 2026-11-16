# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Wolfram ``.m`` file reaches the Wolfram pass (WI-siruf).

``.m`` is shared with MATLAB and Objective-C, so the taxonomy cannot glob it
for Wolfram; the analyzer's own finder (``find_wolfram_files``) takes the
``.m`` files whose content is Wolfram. The file-presence pre-filter, though,
counts Wolfram files with the taxonomy's globs (``*.wl``, ``*.wls``, ``*.nb``),
so a package written as ``.m`` files counted zero, the pass was skipped as
``no_candidate_files``, and no ``.m`` file was ever analysed. Registering the
finder makes the profile count what the pass reads (INV-hokig's cure).
Production path: the real profile and the real dispatcher.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.all_analyzers import run_all_analyzers
from hypergumbo_core.profile import detect_profile

_PACKAGE = (
    'BeginPackage["Geometry`"]\n'
    "area::usage = \"area[r] is the area of a circle\"\n"
    'Begin["`Private`"]\n'
    "area[r_] := Pi r^2\n"
    "End[]\n"
    "EndPackage[]\n"
)


def _wolfram(symbols: list) -> list:
    return [s for s in symbols if "wolfram" in (s.origin if isinstance(s.origin, list) else [s.origin])]


def test_a_dot_m_only_wolfram_package_is_analysed(tmp_path: Path) -> None:
    (tmp_path / "Geometry.m").write_text(_PACKAGE, encoding="utf-8")
    profile = detect_profile(tmp_path, count_loc=True).to_dict()
    assert profile["languages"].get("wolfram", {}).get("files") == 1, profile["languages"]

    _runs, symbols, _edges, _ucs, limits, _cap, _dep = run_all_analyzers(tmp_path, profile=profile)

    assert "wolfram" not in {entry["pass"] for entry in limits.skipped_passes}
    assert "area" in {s.name for s in _wolfram(symbols)}


def test_a_matlab_dot_m_file_is_not_counted_as_wolfram(tmp_path: Path) -> None:
    """The registered finder keeps the content gate: a MATLAB ``.m`` file is
    not Wolfram to the profile either."""
    (tmp_path / "solve.m").write_text("function y = solve(x)\n  % square it\n  y = x .^ 2;\nend\n")
    profile = detect_profile(tmp_path, count_loc=True).to_dict()
    assert profile["languages"].get("wolfram", {}).get("files", 0) == 0, profile["languages"]
