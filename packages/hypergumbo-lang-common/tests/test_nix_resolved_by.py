# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Nix's call edges (audit 0030, WI-hirol).

The one site binds a call through the run-wide resolver with no hint: the
lookup's own value (``name``) when resolved, nothing on the stand-in.
"""
from pathlib import Path

from hypergumbo_lang_common.nix import analyze_nix_files


def test_resolver_route_is_name(tmp_path: Path) -> None:
    (tmp_path / "lib.nix").write_text("{\n  helper = x: x;\n  run = y: helper y;\n}\n")
    (tmp_path / "other.nix").write_text("{\n  go = z: missingFn z;\n}\n")
    calls = [e for e in analyze_nix_files(tmp_path).edges if e.edge_type == "calls"]
    resolved = [e for e in calls if ":helper:" in e.dst]
    external = [e for e in calls if ":missingFn:" in e.dst]
    assert len(resolved) == 1 and len(external) == 1
    assert resolved[0].meta["resolved_by"] == "name"
    assert not external[0].is_resolved
    assert "resolved_by" not in (external[0].meta or {})
