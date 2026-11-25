# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on WGSL's call edges (audit 0030, WI-hirol).

The one site binds a call through the run-wide resolver with no hint: the
lookup's own value, ``name``. A builtin goes to ``make_unresolved_edge`` and
carries no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.wgsl import analyze_wgsl_files


def test_resolver_route_is_name(tmp_path: Path) -> None:
    (tmp_path / "a.wgsl").write_text(
        "fn helper(x: f32) -> f32 { return x; }\n"
        "@fragment\nfn main() -> @location(0) vec4<f32> {\n"
        "  let y = helper(1.0);\n  let z = abs(y);\n  return vec4<f32>(z, z, z, 1.0);\n}\n"
    )
    calls = [e for e in analyze_wgsl_files(tmp_path).edges if e.edge_type == "calls"]
    resolved = [e for e in calls if ":helper:" in e.dst]
    builtin = [e for e in calls if ":abs:" in e.dst]
    assert len(resolved) == 1 and len(builtin) == 1
    assert resolved[0].meta["resolved_by"] == "name"
    assert "resolved_by" not in (builtin[0].meta or {})
