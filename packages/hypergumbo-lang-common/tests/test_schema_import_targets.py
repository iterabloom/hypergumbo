# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/proto.py
# covers: packages/hypergumbo-lang-common/src/hypergumbo_lang_common/thrift.py
"""A proto import or a thrift include ends on the imported file's node.

The dst used to be ``f"<lang>:<import string>:1-1:file:file"``: the string as
written in the path slot. An import spelled from a root below the repository's
(``import "api/v1/b.proto"`` for ``proto/api/v1/b.proto``), or relative
(``include "../shared.thrift"``), named a second file node at a path no file
has, and an import of a file outside the tree (``google/protobuf/empty.proto``)
got a phantom file node. Now the dst is the file's node when the tree holds
it (``edge_target.in_tree_file_id`` over ``search_path_candidates``) and the
``make_module_import_dst`` stand-in otherwise.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_module_import_dst
from hypergumbo_core.analyze.edge_target import non_id_edge_endpoints
from hypergumbo_lang_common.proto import analyze_proto
from hypergumbo_lang_common.thrift import analyze_thrift


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _imports(result, src_rel: str, lang: str) -> dict[str, tuple[str, bool]]:
    """``{dst: (edge_type, is_resolved)}`` of the import edges of one file."""
    src = make_file_id(lang, src_rel)
    return {
        e.dst: (e.edge_type, e.is_resolved) for e in result.edges
        if e.src == src and e.edge_type in ("imports", "includes")
    }


def test_proto_import_ends_on_the_imported_files_node(tmp_path: Path) -> None:
    _write(tmp_path, "proto/api/v1/a.proto", (
        'syntax = "proto3";\npackage api.v1;\n'
        'import "api/v1/b.proto";\n'
        'import "google/protobuf/empty.proto";\n'
        'message A { string x = 1; }\n'
    ))
    _write(tmp_path, "proto/api/v1/b.proto", 'syntax = "proto3";\nmessage B { string y = 1; }\n')
    # Control: an import already spelled from the repository root.
    _write(tmp_path, "proto/common/c.proto", (
        'syntax = "proto3";\nimport "proto/api/v1/b.proto";\nmessage C { string z = 1; }\n'
    ))
    result = analyze_proto(tmp_path)

    a = _imports(result, "proto/api/v1/a.proto", "proto")
    assert len(a) == 2  # reach: both imports are edges
    assert a[make_file_id("proto", "proto/api/v1/b.proto")] == ("imports", True)
    stand_in = make_module_import_dst("proto", "google/protobuf/empty.proto")
    assert a[stand_in] == ("imports", False)
    assert "proto:api/v1/b.proto:1-1:file:file" not in a
    assert "proto:google/protobuf/empty.proto:1-1:file:file" not in a

    c = _imports(result, "proto/common/c.proto", "proto")
    assert c == {make_file_id("proto", "proto/api/v1/b.proto"): ("imports", True)}
    assert non_id_edge_endpoints(result.symbols, result.edges) == []


def test_thrift_include_ends_on_the_included_files_node(tmp_path: Path) -> None:
    _write(tmp_path, "thrift/main.thrift", (
        'include "shared.thrift"\ninclude "sub/types.thrift"\n'
        'include "missing.thrift"\nstruct Main { 1: i32 x }\n'
    ))
    _write(tmp_path, "thrift/shared.thrift", "struct Shared { 1: i32 y }\n")
    _write(tmp_path, "thrift/sub/types.thrift", (
        'include "../shared.thrift"\nstruct T { 1: i32 z }\n'
    ))
    result = analyze_thrift(tmp_path)

    main = _imports(result, "thrift/main.thrift", "thrift")
    assert len(main) == 3
    assert main[make_file_id("thrift", "thrift/shared.thrift")] == ("imports", True)
    assert main[make_file_id("thrift", "thrift/sub/types.thrift")] == ("imports", True)
    assert main[make_module_import_dst("thrift", "missing.thrift")] == ("imports", False)

    sub = _imports(result, "thrift/sub/types.thrift", "thrift")
    assert sub == {make_file_id("thrift", "thrift/shared.thrift"): ("imports", True)}
    assert non_id_edge_endpoints(result.symbols, result.edges) == []
