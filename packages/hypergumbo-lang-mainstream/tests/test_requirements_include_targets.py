# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/requirements.py
"""A ``-r`` / ``-c`` line ends on the named file's node only when the tree
holds that file.

The dst was ``make_file_id("requirements", <path joined onto the including
file's directory>)`` whatever was on disk, so ``-r missing.txt`` (a file the
build generates, or a typo) got a phantom file node at a path no file has.
Now a file that is not in the tree is the ``make_module_import_dst`` stand-in
for the path as written.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import make_file_id, make_module_import_dst
from hypergumbo_core.analyze.edge_target import non_id_edge_endpoints
from hypergumbo_lang_mainstream.requirements import analyze_requirements


def test_requirements_include_ends_on_a_file_of_the_tree_or_a_stand_in(tmp_path: Path) -> None:
    sub = tmp_path / "req" / "sub"
    sub.mkdir(parents=True)
    (sub / "requirements.txt").write_text(
        "-r base.txt\n-r missing.txt\n-c ../constraints.in\nflask==2.0\n"
    )
    (sub / "base.txt").write_text("requests\n")
    (tmp_path / "req" / "constraints.in").write_text("requests<3\n")
    result = analyze_requirements(tmp_path)

    src = make_file_id("requirements", "req/sub/requirements.txt")
    refs = {e.dst: (e.edge_type, e.is_resolved) for e in result.edges
            if e.src == src and e.edge_type in ("includes", "constrains")}
    assert len(refs) == 3  # reach: each option is an edge
    # Control: files of the tree keep the node they had.
    assert refs[make_file_id("requirements", "req/sub/base.txt")] == ("includes", True)
    assert refs[make_file_id("requirements", "req/constraints.in")] == ("constrains", True)
    assert refs[make_module_import_dst("requirements", "missing.txt")] == ("includes", False)
    assert make_file_id("requirements", "req/sub/missing.txt") not in refs
    assert non_id_edge_endpoints(result.symbols, result.edges) == []
