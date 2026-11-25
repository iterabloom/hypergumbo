# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on GLSL's call edges (audit 0030, WI-hirol).

The one site binds a call through the run-wide resolver with no hint: the
lookup's own value, ``name``. A builtin goes to ``make_unresolved_edge`` and
carries no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.glsl import analyze_glsl_files


def test_resolver_route_is_name(tmp_path: Path) -> None:
    (tmp_path / "lib.glsl").write_text("float helper(float x) { return x; }\n")
    (tmp_path / "main.frag").write_text(
        "float helper(float x);\nvoid main() { float y = helper(1.0); y = abs(y); }\n"
    )
    calls = [e for e in analyze_glsl_files(tmp_path).edges if e.edge_type == "calls"]
    resolved = [e for e in calls if ":helper:" in e.dst]
    builtin = [e for e in calls if ":abs:" in e.dst]
    assert len(resolved) == 1 and len(builtin) == 1
    assert resolved[0].meta["resolved_by"] == "name"
    assert "resolved_by" not in (builtin[0].meta or {})
