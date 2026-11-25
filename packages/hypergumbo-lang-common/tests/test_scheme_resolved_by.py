# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Scheme's call edges (audit 0030, WI-hirol).

A call found in the run-wide name table is ``name``; a name no file defines
is the unresolved stand-in and carries no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.scheme import analyze_scheme


def test_table_hit_is_name_and_miss_is_unmarked(tmp_path: Path) -> None:
    (tmp_path / "utils.scm").write_text("(define (helper x) x)\n")
    (tmp_path / "main.scm").write_text("(define (run) (helper 1) (missing-fn 2))\n")
    calls = [e for e in analyze_scheme(tmp_path).edges if e.edge_type == "calls"]
    resolved = [e for e in calls if ":helper:" in e.dst]
    external = [e for e in calls if ":missing-fn:" in e.dst]
    assert len(resolved) == 1 and len(external) == 1
    assert resolved[0].meta["resolved_by"] == "name"
    assert "resolved_by" not in (external[0].meta or {})
