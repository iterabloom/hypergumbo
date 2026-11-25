# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Starlark's call edges (audit 0030, WI-hirol).

The one site binds a call through the run-wide resolver (a ``load`` alias
as the path hint): the lookup's own value when resolved, ``name`` here; a
builtin or an external rule is the stand-in and carries no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.starlark import analyze_starlark


def test_resolver_route_is_name(tmp_path: Path) -> None:
    (tmp_path / "helpers.bzl").write_text("def helper(x):\n    return x\n")
    (tmp_path / "rules.bzl").write_text(
        'load(":helpers.bzl", "helper")\n\n'
        "def my_rule(name):\n    helper(name)\n    native.genrule(name = name)\n"
        "    missing_fn(name)\n"
    )
    calls = [e for e in analyze_starlark(tmp_path).edges if e.edge_type == "calls"]
    resolved = [e for e in calls if ":helper:" in e.dst]
    external = [e for e in calls if ":missing_fn:" in e.dst]
    assert len(resolved) == 1 and len(external) == 1
    assert resolved[0].meta["resolved_by"] == "name"
    assert not external[0].is_resolved
    assert "resolved_by" not in (external[0].meta or {})
