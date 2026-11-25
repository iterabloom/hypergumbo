# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on HLSL's call edges (audit 0030, WI-hirol).

A call the run-wide resolver finds takes the lookup's own value (``name``);
an intrinsic is the unresolved stand-in and carries no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.hlsl import analyze_hlsl


def test_resolved_call_is_marked_and_intrinsic_is_not(tmp_path: Path) -> None:
    (tmp_path / "a.hlsl").write_text(
        "float helper(float x) { return x; }\n"
        "float4 main() : SV_Target { float y = helper(1.0); return saturate(y); }\n"
    )
    calls = [e for e in analyze_hlsl(tmp_path).edges if e.edge_type == "calls"]
    resolved = [e for e in calls if ":helper:" in e.dst]
    intrinsic = [e for e in calls if ":saturate:" in e.dst]
    assert len(resolved) == 1 and len(intrinsic) == 1
    assert resolved[0].meta["resolved_by"] == "name"
    assert not intrinsic[0].is_resolved
    assert "resolved_by" not in (intrinsic[0].meta or {})
