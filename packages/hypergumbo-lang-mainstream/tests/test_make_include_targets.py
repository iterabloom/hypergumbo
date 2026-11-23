# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/make.py
"""A Makefile ``include`` ends on the included file's node.

The dst used to be ``f"makefile:<name>:1-1:file:file"``, the name as written:
``include rules.mk`` in ``mk/Makefile`` named a file node at ``rules.mk``
(the file is ``mk/rules.mk``), ``include ../rules.mk`` one at ``../rules.mk``,
and ``-include missing.mk`` a phantom file node. GNU make reads a relative name
from the directory it runs in, which is the Makefile's own (``make -C``) or,
for a fragment included from the top-level Makefile, the repository root.
Now the dst is the file's node when the tree holds it and the
``make_module_import_dst`` stand-in otherwise.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_module_import_dst
from hypergumbo_core.analyze.edge_target import non_id_edge_endpoints
from hypergumbo_lang_mainstream.make import analyze_make_files


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_make_include_ends_on_the_included_files_node(tmp_path: Path) -> None:
    _write(tmp_path, "mk/Makefile", (
        "include rules.mk\ninclude sub/sub.mk\n-include missing.mk\n"
        "include mk/GNUmakefile\nall:\n\techo hi\n"
    ))
    _write(tmp_path, "mk/rules.mk", "CC = gcc\n")
    _write(tmp_path, "mk/GNUmakefile", "LD = ld\n")
    _write(tmp_path, "mk/sub/sub.mk", "include ../rules.mk\nsub:\n\techo sub\n")
    result = analyze_make_files(tmp_path)

    src = make_file_id("makefile", "mk/Makefile")
    top = {e.dst: e.is_resolved for e in result.edges
           if e.src == src and e.edge_type == "includes"}
    assert len(top) == 4  # reach: every include is an edge
    assert top[make_file_id("makefile", "mk/rules.mk")] is True
    assert top[make_file_id("makefile", "mk/sub/sub.mk")] is True
    # Spelled from the repository root; a name the taxonomy does not list,
    # read by this analyzer, keeps the analyzer's language.
    assert top[make_file_id("makefile", "mk/GNUmakefile")] is True
    assert top[make_module_import_dst("makefile", "missing.mk")] is False

    sub_src = make_file_id("makefile", "mk/sub/sub.mk")
    sub = {e.dst: e.is_resolved for e in result.edges
           if e.src == sub_src and e.edge_type == "includes"}
    assert sub == {make_file_id("makefile", "mk/rules.mk"): True}
    assert non_id_edge_endpoints(result.symbols, result.edges) == []
