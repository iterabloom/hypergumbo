# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Fortran's call edges (audit 0030, WI-hirol).

A ``call`` the run-wide resolver finds takes the lookup's own value
(``name``); a subroutine no file declares is the unresolved stand-in and
carries no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.fortran import analyze_fortran_files


def test_resolved_call_is_marked_and_external_is_not(tmp_path: Path) -> None:
    (tmp_path / "a.f90").write_text(
        "subroutine helper()\nend subroutine helper\n\n"
        "program main\n  call helper()\n  call missing()\nend program main\n"
    )
    result = analyze_fortran_files(tmp_path)
    calls = [e for e in result.edges if e.edge_type == "calls"]
    resolved = [e for e in calls if ":helper:" in e.dst]
    external = [e for e in calls if ":missing:" in e.dst]
    assert len(resolved) == 1 and len(external) == 1
    assert resolved[0].is_resolved and resolved[0].meta["resolved_by"] == "name"
    assert not external[0].is_resolved
    assert "resolved_by" not in (external[0].meta or {})
