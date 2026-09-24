# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag, swift: a call edge's src is the declaration whose span contains it.

The enclosing lookup keyed on the qualified name, which every overload of a
method shares: Alamofire's ``Session.webSocketRequest`` and ``HTTPHeaders.add``.
On a pinned 26-repo run, 261 of 8,343 swift call edges were anchored to an
overload that does not contain the call. Found by position now.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.swift import analyze_swift


def _anchors(tmp_path: Path, source: str) -> list[tuple[str, int, int, int]]:
    (tmp_path / "A.swift").write_text(source)
    result = analyze_swift(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    return [
        (by_id[e.src].name, by_id[e.src].span.start_line, by_id[e.src].span.end_line, e.line)
        for e in result.edges
        if e.edge_type == "calls" and e.line and e.src in by_id
    ]


def test_overloads_are_told_apart(tmp_path: Path) -> None:
    anchors = _anchors(tmp_path, """\
func one() -> Int { return 1 }
func two() -> Int { return 2 }

class Headers {
    func add(name: String) {
        _ = one()
    }

    func add(header: Int) {
        _ = two()
    }

    var count: Int {
        return one()
    }
}
""")
    assert {a[3] for a in anchors} >= {6, 10, 14}, anchors  # reach: all three calls
    for name, start, end, line in anchors:
        assert start <= line <= end, (name, start, end, line)
