# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Swift instance-method call on a typed receiver carries the receiver's TYPE in
the module slot, and an untyped one never carries the receiver's variable name
(INV-kotob; the swift half of WI-higob).

Before this, ``gate_path_hint`` fell through ``receiver_type`` and the import
alias to ``receiver_hint`` -- the receiver's VARIABLE NAME -- so a call on a
local ``fm`` shipped ``dst_ref.module_path == "fm"``. Sized on Alamofire: 42% of
unresolved method-call edges carried such a name. A consumer reading the slot as
a module hint refuses a present-but-wrong hint outright, so that shape is worse
than no hint. The id's module slot said ``external`` at the same site while
dst_ref said ``fm``: two answers to one question, now one answer written to both
by ``make_unresolved_edge``.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.swift import analyze_swift


def _edges(root: Path, src: str, name: str = "app.swift") -> list[Edge]:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(src)
    return analyze_swift(root).edges


def _call(edges: list[Edge], method: str) -> Edge:
    hits = [e for e in edges if e.edge_type == "calls" and e.dst.endswith(f":{method}:unresolved")]
    assert len(hits) == 1, [e.dst for e in edges if method in e.dst]
    return hits[0]


class TestTypedReceiverCarriesItsType:
    def test_declared_parameter(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "p", (
            "import Foundation\n"
            "func check(fm: FileManager, p: String) -> Bool {\n"
            "    return fm.fileExists(atPath: p)\n"
            "}\n"
        ))
        e = _call(edges, "fileExists")
        assert e.dst == "swift:FileManager:0-0:fileExists:unresolved", e.dst
        assert e.dst_ref is not None and e.dst_ref.module_path == "FileManager"
        assert (e.meta or {}).get("receiver_type_hint") == "FileManager"
        assert (e.meta or {}).get("call_construct") == "method"
        assert tag_io_boundaries(edges, {"swift": load_catalog("swift")}) >= 1

    def test_annotated_local(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "a", (
            "import Foundation\n"
            "func check(p: String) -> Bool {\n"
            "    let fm: FileManager = FileManager.default\n"
            "    return fm.fileExists(atPath: p)\n"
            "}\n"
        ))
        assert _call(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"

    def test_constructed_local(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "c", (
            "import Foundation\n"
            "func fetch(u: URL, c: URLSessionConfiguration) {\n"
            "    let session = URLSession(configuration: c)\n"
            "    session.dataTask(with: u)\n"
            "}\n"
        ))
        e = _call(edges, "dataTask")
        assert e.dst == "swift:URLSession:0-0:dataTask:unresolved", e.dst
        assert e.dst_ref is not None and e.dst_ref.module_path == "URLSession"


class TestUntypedReceiverNeverShipsItsName:
    def test_unknown_local(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "u", (
            "struct Handler {\n"
            "  func run() {\n"
            "    let obj = makeThing()\n"
            "    obj.create()\n"
            "  }\n"
            "}\n"
        ))
        e = _call(edges, "create")
        assert e.dst == "swift:external:0-0:create:unresolved", e.dst
        assert e.dst_ref is None or e.dst_ref.module_path not in ("obj", "")
        assert "receiver_type_hint" not in (e.meta or {})

    def test_project_class_receiver_stays_out_of_the_module_slot(self, tmp_path: Path) -> None:
        """A PROJECT type is a symbol, not a module: the hint rides in meta only."""
        edges = _edges(tmp_path / "pc", (
            "struct Store { func save() {} }\n"
            "func go(s: Store) {\n"
            "    s.missing()\n"
            "}\n"
        ))
        e = _call(edges, "missing")
        assert (e.meta or {}).get("receiver_type_hint") == "Store"
        assert e.dst == "swift:external:0-0:missing:unresolved", e.dst


class TestTypeHeadsAndSingletons:
    """The two idioms the first arm-B read-back lost boundaries on."""

    def test_inline_singleton_chain(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "sc", (
            "import Foundation\n"
            "func check(p: String) -> Bool {\n"
            "    return FileManager.default.fileExists(atPath: p)\n"
            "}\n"
        ))
        e = _call(edges, "fileExists")
        assert e.dst == "swift:FileManager:0-0:fileExists:unresolved", e.dst
        assert e.dst_ref is not None and e.dst_ref.module_path == "FileManager"

    def test_singleton_bound_to_a_local(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "sl", (
            "import Foundation\n"
            "func check(p: String) -> Bool {\n"
            "    let fileManager = FileManager.default\n"
            "    return fileManager.fileExists(atPath: p)\n"
            "}\n"
        ))
        assert _call(edges, "fileExists").dst == "swift:FileManager:0-0:fileExists:unresolved"

    def test_uppercase_variable_bound_to_a_project_singleton(self, tmp_path: Path) -> None:
        """``let AF = Session.default`` then ``AF.missing(...)``: AF is a ``Session``
        (a project type), so the hint says so and the slot stays ``external``."""
        edges = _edges(tmp_path / "af", (
            "class Session { func request(_ u: String) {} }\n"
            "let AF = Session.zzz\n"
            "func go(u: String) {\n"
            "    AF.missing(u)\n"
            "}\n"
        ))
        e = _call(edges, "missing")
        assert e.dst == "swift:external:0-0:missing:unresolved", e.dst
        assert e.dst_ref is None
        assert (e.meta or {}).get("receiver_type_hint") == "Session"

    def test_untyped_uppercase_variable_is_not_a_type(self, tmp_path: Path) -> None:
        """``let AF = makeSession()``: capitalised, declared, untyped -- not a type head."""
        edges = _edges(tmp_path / "af2", (
            "let AF = makeSession()\n"
            "func go(u: String) {\n"
            "    AF.missing(u)\n"
            "}\n"
        ))
        e = _call(edges, "missing")
        assert e.dst == "swift:external:0-0:missing:unresolved", e.dst
        assert e.dst_ref is None
        assert "receiver_type_hint" not in (e.meta or {})

    def test_generic_project_type_stays_out_of_the_slot(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "gp", (
            "final class Protected<T> { func read() -> T { fatalError() } }\n"
            "func go(state: Protected<Int>) {\n"
            "    state.missing()\n"
            "}\n"
        ))
        e = _call(edges, "missing")
        assert e.dst == "swift:external:0-0:missing:unresolved", e.dst
        # the parameter parser already strips the generic arguments
        assert (e.meta or {}).get("receiver_type_hint") == "Protected"

    def test_generic_external_type_names_its_head(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "ge", (
            "func go(codes: Set<Int>) {\n"
            "    codes.missing()\n"
            "}\n"
        ))
        assert _call(edges, "missing").dst == "swift:Set:0-0:missing:unresolved"


class TestInstanceNeverBindsAStaticMember:
    def test_instance_call_keeps_the_external_type(self, tmp_path: Path) -> None:
        """A test-only ``static func removeItem(at:)`` extension must not capture
        ``fileManager.removeItem(at:)`` -- the pass-2 read-back's lost boundary."""
        root = tmp_path / "st"
        root.mkdir()
        (root / "Ext.swift").write_text(
            "import Foundation\n"
            "extension FileManager {\n"
            "    static func removeItem(at url: URL) -> Bool { return true }\n"
            "}\n"
        )
        (root / "app.swift").write_text(
            "import Foundation\n"
            "func clean(u: URL) {\n"
            "    let fileManager = FileManager.default\n"
            "    fileManager.removeItem(at: u)\n"
            "    FileManager.removeItem(at: u)\n"
            "}\n"
        )
        edges = analyze_swift(root).edges
        hits = [e for e in edges if e.edge_type == "calls" and "clean" in e.src and "removeItem" in e.dst]
        assert len(hits) == 2, [e.dst for e in hits]
        by_line = {e.line: e for e in hits}
        assert by_line[4].dst == "swift:FileManager:0-0:removeItem:unresolved", by_line[4].dst
        assert by_line[5].is_resolved and by_line[5].dst.endswith(":FileManager.removeItem:method")
