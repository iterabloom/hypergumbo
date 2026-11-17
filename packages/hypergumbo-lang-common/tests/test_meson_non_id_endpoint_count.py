# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: a Meson ``subdir()`` edge whose dst is written by hand is
counted on the meson pass's run in the saved map.

The meson analyzer writes ``meson:subdir:<dir>`` as the dst of the edge a
``subdir()`` call makes, a string that is not a node id. This test pins that
``hypergumbo run`` reports it in ``analysis_runs[]`` (the count and the
spelling), read back from the map on disk. When the analyzer is fixed to
look the directory's ``meson.build`` up, the expected count becomes 0.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.survey_io import load_substrate


def test_the_meson_pass_reports_its_subdir_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "meson.build").write_text("project('demo', 'c')\nsubdir('src')\n")
    (repo / "src" / "meson.build").write_text("executable('demo', 'main.c')\n")
    (repo / "src" / "main.c").write_text("int main(void) { return 0; }\n")
    out = tmp_path / "survey.json"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    main([
        "run", str(repo), "--out", str(out),
        "--no-sketch-fan-out", "--no-handler-slices",
    ])

    runs = load_substrate(out)["analysis_runs"]
    [meson] = [r for r in runs if r["pass"].startswith("meson")]
    assert meson["non_id_edge_endpoints"] == 1
    assert any("'meson:subdir:src'" in w for w in meson["warnings"])
    c_runs = [r for r in runs if r["pass"] == "c"]
    assert c_runs and all(r["non_id_edge_endpoints"] == 0 for r in c_runs)
