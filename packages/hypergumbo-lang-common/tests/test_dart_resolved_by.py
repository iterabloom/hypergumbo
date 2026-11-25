# SPDX-License-Identifier: AGPL-3.0-or-later
"""``resolved_by`` on Dart's call-family edges (audit 0030, WI-hirol).

One fixture per route: a method looked up on a parameter's declared type is
``receiver_type``; a bare call bound to a method of the caller's own class is
``own_type`` (``resolved_by_for_bare_call``); a bare call to a free function
and a construction take the resolver lookup's own value (``name`` here).
The inheritance edges (``_heritage_edges``, edge type from the clause) are
never call-family and carry no mark.
"""
from pathlib import Path

from hypergumbo_lang_common.dart import analyze_dart


def _edge(result, src: str, dst: str, edge_type: str = "calls"):
    found = [
        e for e in result.edges
        if e.edge_type == edge_type and f":{src}:" in e.src and f":{dst}:" in e.dst
    ]
    assert len(found) == 1, f"{edge_type} {src} -> {dst}: {len(found)} edge(s)"
    return found[0]


def test_dart_routes(tmp_path: Path) -> None:
    (tmp_path / "db.dart").write_text("class Database {\n  void save(String x) {}\n}\n")
    (tmp_path / "main.dart").write_text(
        "import 'db.dart';\n\n"
        "void helper() {}\n\n"
        "class Runner extends Database {\n"
        "  void tick() {}\n"
        "  void go(Database db) {\n"
        "    db.save('x');\n"
        "    tick();\n"
        "    helper();\n"
        "    var d = new Database();\n"
        "  }\n"
        "}\n"
    )
    result = analyze_dart(tmp_path)
    assert _edge(result, "Runner.go", "Database.save").meta["resolved_by"] == "receiver_type"
    assert _edge(result, "Runner.go", "Runner.tick").meta["resolved_by"] == "own_type"
    assert _edge(result, "Runner.go", "helper").meta["resolved_by"] == "name"
    made = _edge(result, "Runner.go", "Database", edge_type="instantiates")
    assert made.meta["resolved_by"] == "name"
    extends = [e for e in result.edges if e.edge_type == "extends"]
    assert extends
    assert all("resolved_by" not in (e.meta or {}) for e in extends)
