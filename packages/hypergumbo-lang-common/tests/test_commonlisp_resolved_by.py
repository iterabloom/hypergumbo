# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Common Lisp's call edges (audit 0030, WI-hirol).

The one site binds every call through the run-wide resolver with no hint, so
the mark is the lookup's own value: ``name``.
"""
from pathlib import Path

from hypergumbo_lang_common.commonlisp import analyze_commonlisp


def test_resolver_route_is_name(tmp_path: Path) -> None:
    (tmp_path / "util.lisp").write_text("(defun helper (x) x)\n")
    (tmp_path / "main.lisp").write_text("(defun run () (helper 1))\n")
    result = analyze_commonlisp(tmp_path)
    found = [
        e for e in result.edges
        if e.edge_type == "calls" and ":run:" in e.src and ":helper:" in e.dst
    ]
    assert len(found) == 1
    assert found[0].meta["resolved_by"] == "name"
