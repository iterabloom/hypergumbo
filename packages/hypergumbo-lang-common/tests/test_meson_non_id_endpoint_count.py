# SPDX-License-Identifier: AGPL-3.0-or-later
"""End to end: the edge a Meson ``subdir()`` makes is counted as an id on the
meson pass's run in the saved map, and ends on the subdirectory's build file.

The meson analyzer wrote ``meson:subdir:<dir>`` as the dst of that edge, a
string that is not a node id; ``hypergumbo run`` reported it in
``analysis_runs[]`` (count 1, and the spelling in a warning). The dst is now
the ``meson.build`` file node of the directory (WI-tatop), so the count read
back from the map on disk is 0 and no warning names an endpoint.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import make_file_id
from hypergumbo_core.cli import main
from hypergumbo_core.survey_io import load_substrate


def test_the_meson_pass_reports_no_non_id_endpoint(
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

    data = load_substrate(out)
    [meson] = [r for r in data["analysis_runs"] if r["pass"].startswith("meson")]
    assert meson["non_id_edge_endpoints"] == 0
    assert not any("not node ids" in w for w in meson.get("warnings", []))
    subdir = [e for e in data["edges"] if e["type"] == "includes"]
    assert [e["dst"] for e in subdir] == [make_file_id("meson", "src/meson.build")]
    c_runs = [r for r in data["analysis_runs"] if r["pass"] == "c"]
    assert c_runs and all(r["non_id_edge_endpoints"] == 0 for r in c_runs)
