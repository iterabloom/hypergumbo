# SPDX-License-Identifier: AGPL-3.0-or-later
"""An Objective-C local bound to a message RESULT, and a NESTED receiver, take the
callee's declared return class from the return-type registry (WI-higob, objc).

Pass 1 registers ``<Class>.<selector> -> class`` for every ``- (T *)sel``
(``instancetype`` resolves to the class; ``id``, primitives and ``void`` do
not); Pass 2 reads it for ``id x = [obj sel]`` and for ``[[obj sel] frob]``.
Library rows arrive through the same dict (WI-lalot), so one fixture feeds a
row by hand through the edge pass directly.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.objc import analyze_objc, is_objc_tree_sitter_available
from test_objc_dst_ref import _check_grammar_or_skip


def _edges(root: Path, files: dict[str, str]) -> list[Edge]:
    _check_grammar_or_skip(is_objc_tree_sitter_available, "objc")
    root.mkdir(parents=True, exist_ok=True)
    for name, src in files.items():
        (root / name).write_text(src)
    return analyze_objc(root).edges


def _send(edges: list[Edge], selector: str) -> Edge:
    hits = [e for e in edges if e.edge_type == "calls" and not e.is_resolved and e.dst.endswith(f":{selector}:unresolved")]
    assert len(hits) == 1, [e.dst for e in edges if selector in e.dst]
    return hits[0]


FACTORY = (
    "@interface Factory : NSObject\n"
    "- (NSFileManager *)manager;\n"
    "- (instancetype)shared;\n"
    "- (BOOL)flag;\n"
    "@end\n"
    "@implementation Factory\n"
    "- (NSFileManager *)manager { return nil; }\n"
    "- (instancetype)shared { return self; }\n"
    "- (BOOL)flag { return YES; }\n"
    "@end\n"
)
WRAP = "@interface Main : NSObject\n@end\n\n@implementation Main\n%s\n@end\n"


class TestInRepoReturnTypes:
    def test_id_local_bound_to_a_message_result(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "id", {"Factory.m": FACTORY, "Main.m": WRAP % (
            "- (void)run:(Factory *)f path:(NSString *)p {\n"
            "    id m = [f manager];\n"
            "    [m fileExistsAtPath:p];\n"
            "}"
        )})
        e = _send(edges, "fileExistsAtPath:")
        assert e.dst == "objc:NSFileManager:0-0:fileExistsAtPath::unresolved", e.dst
        assert e.dst_ref is not None and e.dst_ref.module_path == "NSFileManager"

    def test_nested_receiver(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "nest", {"Factory.m": FACTORY, "Main.m": WRAP % (
            "- (void)run:(Factory *)f path:(NSString *)p {\n"
            "    [[f manager] fileExistsAtPath:p];\n"
            "}"
        )})
        assert _send(edges, "fileExistsAtPath:").dst == "objc:NSFileManager:0-0:fileExistsAtPath::unresolved"

    def test_instancetype_is_the_owner_and_stays_a_project_hint(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "inst", {"Factory.m": FACTORY, "Main.m": WRAP % (
            "- (void)run:(Factory *)f {\n"
            "    id s = [f shared];\n"
            "    [s frob];\n"
            "}"
        )})
        e = _send(edges, "frob")
        assert e.dst == "objc:external:0-0:frob:unresolved", e.dst
        assert (e.meta or {}).get("receiver_type_hint") == "Factory"

    def test_primitive_and_unknown_results_stay_untyped(self, tmp_path: Path) -> None:
        edges = _edges(tmp_path / "prim", {"Factory.m": FACTORY, "Main.m": WRAP % (
            "- (void)run:(Factory *)f {\n"
            "    id b = [f flag];\n"
            "    [b frob];\n"
            "    id u = [f unknownThing];\n"
            "    [u frob2];\n"
            "    [[f unknownThing] frob3];\n"
            "    id anon = [self make];\n"
            "    [[anon manager] frob4];\n"
            "}"
        )})
        for sel in ("frob", "frob2", "frob3", "frob4"):
            e = _send(edges, sel)
            assert e.dst == f"objc:external:0-0:{sel}:unresolved", e.dst
            assert "receiver_type_hint" not in (e.meta or {})


class TestLibraryRowsFeedTheSameDict:
    def test_a_fed_row_types_a_nested_alloc_init_chain(self, tmp_path: Path) -> None:
        """``[[NSFileManager alloc] init]`` needs Foundation rows WI-lalot supplies;
        fed by hand here, the same path types the outer send."""
        from hypergumbo_core.ir import AnalysisRun
        from hypergumbo_core.symbol_resolution import NameResolver
        from hypergumbo_lang_mainstream import objc as m
        _check_grammar_or_skip(is_objc_tree_sitter_available, "objc")
        root = tmp_path / "lib"
        root.mkdir()
        f = root / "Main.m"
        f.write_text(WRAP % (
            "- (void)run:(NSString *)p {\n"
            "    [[[NSFileManager alloc] init] fileExistsAtPath:p];\n"
            "}"
        ))
        run = AnalysisRun.create(pass_id=m.PASS_ID, version="t")
        parser = m.ObjCAnalyzer()._create_parser()
        analysis = m._extract_symbols_from_file(f, parser, run, "Main.m")
        edges = m._extract_edges_from_file(
            f, parser, analysis.methods_by_name, NameResolver({}), run,
            method_return_types={"NSFileManager.alloc": "NSFileManager", "NSFileManager.init": "NSFileManager"},
        )
        assert _send(edges, "fileExistsAtPath:").dst == "objc:NSFileManager:0-0:fileExistsAtPath::unresolved"
