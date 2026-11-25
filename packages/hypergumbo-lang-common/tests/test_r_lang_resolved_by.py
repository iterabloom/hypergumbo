# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on R's call edges (audit 0030, WI-hirol).

``pkga::helper``: the package the call site names is passed as the path
hint; when it is that hint that picks the candidate, the edge is
``qualified`` (a closest fit: the package is matched as a substring of the
file's path). A plain call takes the lookup's value (``name``). A miss of
either form carries no mark.

The fixture shows the only way the hint is read: R names may contain dots,
the resolver's suffix match splits ``a.helper`` at the dot, and with no
exact ``helper`` key the two ``*.helper`` candidates are told apart by the
hint.
"""
from pathlib import Path

from hypergumbo_lang_common.r_lang import analyze_r_files


def test_r_routes(tmp_path: Path) -> None:
    (tmp_path / "pkga" / "R").mkdir(parents=True)
    (tmp_path / "pkgb" / "R").mkdir(parents=True)
    (tmp_path / "pkga" / "R" / "a.R").write_text("a.helper <- function(x) x\n")
    (tmp_path / "pkgb" / "R" / "b.R").write_text(
        "b.helper <- function(x) x\nplain <- function(x) x\n"
        "caller <- function(y) {\n  pkga::helper(y)\n  stats::median(y)\n"
        "  plain(y)\n  print(y)\n}\n"
    )
    calls = {
        e.dst.split(":")[-2]: e for e in analyze_r_files(tmp_path).edges
        if e.edge_type == "calls" and ":caller:" in e.src
    }
    assert calls["a.helper"].meta["resolved_by"] == "qualified"
    assert calls["plain"].meta["resolved_by"] == "name"
    for missed in ("median", "print"):
        assert not calls[missed].is_resolved
        assert "resolved_by" not in (calls[missed].meta or {})
