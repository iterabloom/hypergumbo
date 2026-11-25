# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on MATLAB's call edges (audit 0030, WI-hirol).

A call found in the run-wide name table is ``name``; a function no file
declares (``disp``) is the unresolved stand-in and carries no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.matlab import analyze_matlab


def test_table_hit_is_name_and_miss_is_unmarked(tmp_path: Path) -> None:
    (tmp_path / "helper.m").write_text("function y = helper(x)\n    y = x;\nend\n")
    (tmp_path / "main.m").write_text("function run()\n    helper(1);\n    disp(1);\nend\n")
    calls = [e for e in analyze_matlab(tmp_path).edges if e.edge_type == "calls"]
    resolved = [e for e in calls if ":helper:" in e.dst]
    external = [e for e in calls if ":disp:" in e.dst]
    assert len(resolved) == 1 and len(external) == 1
    assert resolved[0].meta["resolved_by"] == "name"
    assert "resolved_by" not in (external[0].meta or {})
