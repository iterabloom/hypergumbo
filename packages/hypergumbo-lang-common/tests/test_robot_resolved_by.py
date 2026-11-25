# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Robot Framework's call edges (audit 0030, WI-hirol).

A keyword invocation found in the run-wide keyword table is ``name``; a
library keyword no file defines is the unresolved stand-in and carries no
mark.
"""
from pathlib import Path

from hypergumbo_lang_common.robot import analyze_robot


def test_table_hit_is_name_and_miss_is_unmarked(tmp_path: Path) -> None:
    (tmp_path / "keywords.robot").write_text(
        "*** Keywords ***\nOpen Login Page\n    Open Browser    http://x\n"
    )
    (tmp_path / "suite.robot").write_text(
        "*** Settings ***\nResource    keywords.robot\n\n"
        "*** Test Cases ***\nValid Login\n    Open Login Page\n"
    )
    calls = [e for e in analyze_robot(tmp_path).edges if e.edge_type == "calls"]
    resolved = [e for e in calls if ":Open Login Page:" in e.dst]
    external = [e for e in calls if ":Open Browser:" in e.dst]
    assert len(resolved) == 1 and len(external) == 1
    assert resolved[0].meta["resolved_by"] == "name"
    assert "resolved_by" not in (external[0].meta or {})
