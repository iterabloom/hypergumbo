# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Elm's call edges (audit 0030, WI-hirol).

The one site binds every call through the run-wide resolver, a qualified
``U.helper`` with its import alias as the path hint: the mark is the lookup's
own value. Both a qualified and a bare call land on an exact key here, so the
value is ``name`` (an exact key is returned before the hint is read).
"""
from pathlib import Path

from hypergumbo_lang_common.elm import analyze_elm


def _edge(result, src: str, dst: str):
    found = [
        e for e in result.edges
        if e.edge_type == "calls" and f":{src}:" in e.src and f":{dst}:" in e.dst
    ]
    assert len(found) == 1, f"calls {src} -> {dst}: {len(found)} edge(s)"
    return found[0]


def test_resolver_route_takes_the_lookup_value(tmp_path: Path) -> None:
    (tmp_path / "Util.elm").write_text("module Util exposing (helper)\n\nhelper x = x\n")
    (tmp_path / "Main.elm").write_text(
        "module Main exposing (run)\n\nimport Util as U\n\n"
        "run x = U.helper x\n\ngo y = run y\n"
    )
    result = analyze_elm(tmp_path)
    assert _edge(result, "run", "helper").meta["resolved_by"] == "name"
    assert _edge(result, "go", "run").meta["resolved_by"] == "name"
