# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Swift call binds a project member only when the member's ARGUMENT LABELS admit
it (INV-fatap).

`swift.py` resolved `recv.method(...)` on a typed receiver as
`f"{type_name}.{callee_name}"` in the symbol tables -- by BARE method name. Swift
identifies a method by its labels, so `removeItem(at:)` and `removeItem(atPath:)` are
different methods, and a project extension declaring one captured a call to the other:
the catalogued Foundation boundary vanished behind a resolved edge to a member the call
cannot compile against. PR #757 closed the static-vs-instance half of the same
mis-binding; this is the label half.

The refusal is deliberately one-sided -- it fires only on a label the call supplies that
the declaration does not have. A defaulted parameter (call omits a label), a trailing
closure (no `value_arguments` at all) and an unlabelled call all still bind, because
those are the shapes where a stricter rule would lose TRUE binds.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.swift import analyze_swift


def _edges(root: Path, src: str) -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.swift").write_text(src)
    return analyze_swift(root).edges


def _call(edges: list[Edge], method: str) -> Edge:
    hits = [e for e in edges if e.edge_type == "calls" and method in e.dst]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0]


class TestLabelIncompatibleMembersDoNotCapture:
    def test_the_filed_repro(self, tmp_path: Path) -> None:
        """`removeItem(atPath:)` must not capture `removeItem(at:)`."""
        edges = _edges(tmp_path / "repro", (
            "import Foundation\n"
            "extension FileManager {\n"
            "    func removeItem(atPath p: String) -> Bool { return true }\n"
            "}\n"
            "func go(fm: FileManager) {\n"
            "    fm.removeItem(at: URL(fileURLWithPath: \"/x\"))\n"
            "}\n"
        ))
        edge = _call(edges, "removeItem")
        assert edge.is_resolved is False, edge.dst
        assert edge.dst == "swift:FileManager:0-0:removeItem:unresolved"

    def test_the_positive_control_still_binds(self, tmp_path: Path) -> None:
        """A label-COMPATIBLE project member must still resolve, in the same shape."""
        edges = _edges(tmp_path / "ctl", (
            "import Foundation\n"
            "extension FileManager {\n"
            "    func createFile(atPath p: String) -> Bool { return true }\n"
            "}\n"
            "func go(fm: FileManager) {\n"
            "    fm.createFile(atPath: \"/x\")\n"
            "}\n"
        ))
        edge = _call(edges, "createFile")
        assert edge.is_resolved is not False, edge.dst
        assert edge.dst.endswith("FileManager.createFile:method")

    def test_an_unlabelled_parameter_admits_an_unlabelled_call(
        self, tmp_path: Path,
    ) -> None:
        """`func drop(_ x: Int)` is called `drop(1)`: the label is absent on BOTH sides."""
        edges = _edges(tmp_path / "wild", (
            "import Foundation\n"
            "extension FileManager {\n"
            "    func drop(_ x: Int) -> Bool { return true }\n"
            "}\n"
            "func go(fm: FileManager) {\n"
            "    fm.drop(1)\n"
            "}\n"
        ))
        assert _call(edges, "drop").dst.endswith("FileManager.drop:method")

    def test_a_defaulted_parameter_admits_a_shorter_call(self, tmp_path: Path) -> None:
        """A call may omit a defaulted argument; that is not evidence against the bind."""
        edges = _edges(tmp_path / "dflt", (
            "import Foundation\n"
            "extension FileManager {\n"
            "    func sweep(atPath p: String, deep d: Bool = false) -> Bool { return true }\n"
            "}\n"
            "func go(fm: FileManager) {\n"
            "    fm.sweep(atPath: \"/x\")\n"
            "}\n"
        ))
        assert _call(edges, "sweep").dst.endswith("FileManager.sweep:method")

    def test_a_trailing_closure_call_supplies_no_labels_and_still_binds(
        self, tmp_path: Path,
    ) -> None:
        edges = _edges(tmp_path / "trail", (
            "import Foundation\n"
            "extension FileManager {\n"
            "    func each(body: () -> Void) { }\n"
            "}\n"
            "func go(fm: FileManager) {\n"
            "    fm.each { }\n"
            "}\n"
        ))
        assert _call(edges, "each").dst.endswith("FileManager.each:method")

    def test_a_global_symbol_is_gated_by_the_same_rule(self, tmp_path: Path) -> None:
        """The cross-file table takes the identical guard as the same-file one."""
        root = tmp_path / "glob"
        root.mkdir(parents=True, exist_ok=True)
        (root / "ext.swift").write_text(
            "import Foundation\n"
            "extension FileManager {\n"
            "    func removeItem(atPath p: String) -> Bool { return true }\n"
            "}\n"
        )
        (root / "use.swift").write_text(
            "import Foundation\n"
            "func go(fm: FileManager) {\n"
            "    fm.removeItem(at: URL(fileURLWithPath: \"/x\"))\n"
            "}\n"
        )
        edges = analyze_swift(root).edges
        hits = [
            e for e in edges
            if e.edge_type == "calls" and e.dst.endswith(":removeItem:unresolved")
        ]
        assert len(hits) == 1, [e.dst for e in edges if "removeItem" in e.dst]
        assert hits[0].dst == "swift:FileManager:0-0:removeItem:unresolved"

    def test_a_call_matching_a_NON_SURVIVING_overload_still_binds(
        self, tmp_path: Path,
    ) -> None:
        """The case that decided the design. Swift keys `local_symbols` by
        `Type.method`, so a second `index` declaration overwrites the first. Asking
        only about the survivor withdrew 291 TRUE binds on Alamofire against 49
        false ones -- `headers.index(of:)` refused because the surviving entry was
        `index(after:)`. The question is asked of EVERY overload."""
        edges = _edges(tmp_path / "ovl", (
            "import Foundation\n"
            "extension FileManager {\n"
            "    func index(after i: Int) -> Int { return i }\n"
            "    func index(of name: String) -> Int { return 0 }\n"
            "}\n"
            "func go(fm: FileManager) {\n"
            "    _ = fm.index(of: \"x\")\n"
            "}\n"
        ))
        edge = _call(edges, "index")
        assert edge.is_resolved is not False, edge.dst
        assert edge.dst.endswith("FileManager.index:method"), edge.dst

    def test_overloads_that_ALL_disagree_still_refuse(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "ovlno", (
            "import Foundation\n"
            "extension FileManager {\n"
            "    func removeItem(atPath p: String) -> Bool { return true }\n"
            "    func removeItem(named n: String) -> Bool { return true }\n"
            "}\n"
            "func go(fm: FileManager) {\n"
            "    fm.removeItem(at: URL(fileURLWithPath: \"/x\"))\n"
            "}\n"
        ))
        edge = _call(edges, "removeItem")
        assert edge.is_resolved is False, edge.dst
        assert edge.dst == "swift:FileManager:0-0:removeItem:unresolved"
