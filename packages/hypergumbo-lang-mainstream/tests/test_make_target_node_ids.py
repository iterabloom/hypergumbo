# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/make.py
"""A target named twice in one rule header is one target node.

``a a: b`` declares the target ``a`` once (Make reads a repeated target in one
header as one). The analyzer emitted one symbol per word of the header, so a
repeated word gave two nodes with one id. flask-restful shows it through a
recipe line indented with spaces, which the grammar reads as a rule header:
``git log --raw | grep "^Author: " | cut -d ' ' -f2- | cut -d '<' -f1`` names
``cut`` and ``-d`` twice each.
"""

from pathlib import Path

from hypergumbo_lang_mainstream.make import analyze_make_files

_MAKEFILE = (
    "a a b: c\n"
    "\techo $@\n"
    "\n"
    "a: d\n"
    "\techo again\n"
    "\n"
    ".PHONY: authors\n"
    "authors:\n"
    "    echo \"Authors\" > AUTHORS.md\n"
    "    git log --raw | grep \"^Author: \" | cut -d ' ' -f2- | cut -d '<' -f1 | sort >> AUTHORS.md\n"
    "\n"
    "register: doc\n"
    "\techo r\n"
)


def test_a_target_repeated_in_one_header_is_one_node(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text(_MAKEFILE)
    result = analyze_make_files(tmp_path)
    targets = [s for s in result.symbols if s.kind == "target"]
    ids = [s.id for s in targets]
    assert len(ids) == len(set(ids))
    # Reach: the first rule's a and b, the second rule's a, and the words
    # of the space-indented recipe lines the grammar reads as one header,
    # cut and -d each once.
    assert sorted(s.name for s in targets if s.span.start_line == 1) == ["a", "b"]
    assert [s.name for s in targets if s.span.start_line == 4] == ["a"]
    header = [s.name for s in targets if s.span.start_line == 9]
    assert header.count("cut") == 1 and header.count("-d") == 1
    assert {"echo", "log", "grep", "-f2-", "-f1"} <= set(header)
    # The repeated target's prerequisite edge is emitted once.
    deps = [e for e in result.edges if e.edge_type == "depends_on" and e.src.endswith(":1-4:a:target")]
    assert len(deps) == 1
