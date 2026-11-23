# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-extended1/src/hypergumbo_lang_extended1/capnp.py
"""A Cap'n Proto import ends on the imported file's node.

The dst used to be ``f"capnp:<import string>:1-1:file:file"``, the string as
written: ``import "../shared.capnp"`` named a file node at ``../shared.capnp``,
``import "/capnp/c++.capnp"`` one at ``/capnp/c++.capnp``. Cap'n Proto reads a
name that starts with ``/`` along its import path (``-I``) and any other name
relative to the importing file. Now the dst is the file's node when the tree
holds it and the ``make_module_import_dst`` stand-in otherwise.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_module_import_dst
from hypergumbo_core.analyze.edge_target import non_id_edge_endpoints
from hypergumbo_lang_extended1.capnp import analyze_capnp


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_capnp_import_ends_on_the_imported_files_node(tmp_path: Path) -> None:
    _write(tmp_path, "schema/main.capnp", (
        "@0xbf5147cbbecf40c1;\n"
        'using Shared = import "shared.capnp";\n'
        'using Sub = import "sub/types.capnp";\n'
        'using Abs = import "/schema/sub/types.capnp";\n'
        'using Cxx = import "/capnp/c++.capnp";\n'
        "struct Main { x @0 :Int32; }\n"
    ))
    _write(tmp_path, "schema/shared.capnp", "@0xbf5147cbbecf40c2;\nstruct Shared { y @0 :Int32; }\n")
    _write(tmp_path, "schema/sub/types.capnp", (
        "@0xbf5147cbbecf40c3;\n"
        'using Up = import "../shared.capnp";\n'
        'using Near = import "shared.capnp";\n'
        "struct T { z @0 :Int32; }\n"
    ))
    result = analyze_capnp(tmp_path)

    src = make_file_id("capnp", "schema/main.capnp")
    edges = [e for e in result.edges if e.src == src and e.edge_type == "imports"]
    assert len(edges) == 4  # reach: the absolute and relative forms are edges
    main = {e.dst: e.is_resolved for e in edges}
    assert len(main) == 3
    assert main[make_file_id("capnp", "schema/shared.capnp")] is True
    # A relative name and an absolute one reach the same file.
    assert main[make_file_id("capnp", "schema/sub/types.capnp")] is True
    assert main[make_module_import_dst("capnp", "/capnp/c++.capnp")] is False

    sub_src = make_file_id("capnp", "schema/sub/types.capnp")
    sub = {e.dst: e.is_resolved for e in result.edges
           if e.src == sub_src and e.edge_type == "imports"}
    assert sub == {
        make_file_id("capnp", "schema/shared.capnp"): True,
        # A relative name is looked up next to the importer only: there is no
        # schema/sub/shared.capnp, and the parent's file is not it.
        make_module_import_dst("capnp", "shared.capnp"): False,
    }
    assert non_id_edge_endpoints(result.symbols, result.edges) == []
