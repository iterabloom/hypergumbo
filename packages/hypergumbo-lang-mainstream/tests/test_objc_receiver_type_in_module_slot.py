# SPDX-License-Identifier: AGPL-3.0-or-later
"""An Objective-C message to a receiver the file DECLARES (``NSFileManager *fm``,
a ``(NSFileManager *)fm`` parameter) carries the declared class in the module
slot, as a class-message receiver already does (WI-higob, objc half).

Before this, only a PascalCase receiver (``[NSFileManager defaultManager]``)
got a module; every lowercase receiver took the bare placeholder even when its
type was written two lines up. Sized on AFNetworking / SDWebImage: 66% / 63% of
message sends carried no module. The catalogue's 123 objc method-kind rows key
by bare class (``NSFileManager``), so a declared type is the whole match.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.io_boundary import load_catalog, tag_io_boundaries
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.objc import analyze_objc, is_objc_tree_sitter_available
from test_objc_dst_ref import _check_grammar_or_skip


def _edges(root: Path, src: str) -> list[Edge]:
    _check_grammar_or_skip(is_objc_tree_sitter_available, "objc")
    root.mkdir(parents=True, exist_ok=True)
    (root / "Main.m").write_text(src)
    return analyze_objc(root).edges


def _send(edges: list[Edge], selector: str) -> Edge:
    hits = [e for e in edges if e.edge_type == "calls" and not e.is_resolved and e.dst.endswith(f":{selector}:unresolved")]
    assert len(hits) == 1, [e.dst for e in edges if selector in e.dst]
    return hits[0]


WRAP = "@interface Main : NSObject\n- (void)run;\n@end\n\n@implementation Main\n%s\n@end\n"


class TestDeclaredReceiverCarriesItsClass:
    def test_declared_local(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "l", WRAP % (
            "- (void)run {\n"
            "    NSFileManager *fm = [NSFileManager defaultManager];\n"
            "    [fm fileExistsAtPath:@\"/tmp/x\"];\n"
            "}"
        ))
        e = _send(edges, "fileExistsAtPath:")
        assert e.dst == "objc:NSFileManager:0-0:fileExistsAtPath::unresolved", e.dst
        assert e.dst_ref is not None and e.dst_ref.module_path == "NSFileManager"
        assert (e.meta or {}).get("receiver_type_hint") == "NSFileManager"
        assert tag_io_boundaries(edges, {"objc": load_catalog("objc")}) >= 1

    def test_declared_parameter(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "p", WRAP % (
            "- (BOOL)check:(NSFileManager *)fm path:(NSString *)p {\n"
            "    return [fm fileExistsAtPath:p];\n"
            "}"
        ))
        assert _send(edges, "fileExistsAtPath:").dst == "objc:NSFileManager:0-0:fileExistsAtPath::unresolved"

    def test_rebinding_takes_the_new_declaration(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "r", WRAP % (
            "- (void)run {\n"
            "    NSFileManager *x = [NSFileManager defaultManager];\n"
            "    [x fileExistsAtPath:@\"/a\"];\n"
            "}\n"
            "- (void)other {\n"
            "    NSString *x = @\"s\";\n"
            "    [x lengthOfBytesUsingEncoding:4];\n"
            "}"
        ))
        assert _send(edges, "fileExistsAtPath:").dst.split(":")[1] == "NSFileManager"
        assert _send(edges, "lengthOfBytesUsingEncoding:").dst.split(":")[1] == "NSString"


class TestUndeclaredReceiverStaysBare:
    def test_id_local(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "u", WRAP % (
            "- (void)run {\n"
            "    id obj = [self make];\n"
            "    [obj frob];\n"
            "}"
        ))
        e = _send(edges, "frob")
        assert e.dst == "objc:external:0-0:frob:unresolved", e.dst
        assert e.dst_ref is None
        assert "receiver_type_hint" not in (e.meta or {})


class TestCategoriesDoNotMakeProjectClasses:
    def test_category_on_a_framework_class_keeps_the_slot(self, tmp_path: Path) -> None:
        """``@implementation NSData (Extras)`` does not make NSData a project class."""
        _check_grammar_or_skip(is_objc_tree_sitter_available, "objc")
        root = tmp_path / "cat"
        root.mkdir()
        (root / "NSData+Extras.m").write_text(
            "@implementation NSData (Extras)\n- (NSData *)sd_copy { return self; }\n@end\n"
        )
        (root / "Main.m").write_text(WRAP % (
            "- (void)run:(NSData *)d {\n"
            "    [d writeToFile:@\"/tmp/x\" atomically:YES];\n"
            "}"
        ))
        result = analyze_objc(root)
        e = _send(result.edges, "writeToFile:atomically:")
        assert e.dst == "objc:NSData:0-0:writeToFile:atomically::unresolved", e.dst
        cat = [s for s in result.symbols if s.kind == "class" and s.name == "NSData"]
        assert cat and (cat[0].meta or {}).get("category") == "Extras"

    def test_declared_project_class_with_a_category_stays_hint_only(self, tmp_path: Path) -> None:
        _check_grammar_or_skip(is_objc_tree_sitter_available, "objc")
        root = tmp_path / "pc"
        root.mkdir()
        (root / "Svc.m").write_text(
            "@interface Svc : NSObject\n@end\n@implementation Svc\n@end\n"
            "@implementation Svc (Extras)\n@end\n"
        )
        (root / "Main.m").write_text(WRAP % (
            "- (void)run:(Svc *)s {\n"
            "    [s frob];\n"
            "}"
        ))
        e = _send(analyze_objc(root).edges, "frob")
        assert e.dst == "objc:external:0-0:frob:unresolved", e.dst
        assert (e.meta or {}).get("receiver_type_hint") == "Svc"
