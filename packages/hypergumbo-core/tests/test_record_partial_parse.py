# SPDX-License-Identifier: AGPL-3.0-or-later
"""A tree that needed error recovery is recorded, with its error-node count.

WI-bulaz: ``has_error`` was on every tree and nothing read it, so a file whose
parse died looked exactly like a file that declares nothing.
"""

from __future__ import annotations

import tree_sitter
from tree_sitter_language_pack import get_language

from hypergumbo_core.analyze.base import PARTIAL_PARSE_REASON, record_partial_parse
from hypergumbo_core.ir import AnalysisRun


def _tree(src: bytes):
    return tree_sitter.Parser(get_language("python")).parse(src)


def test_a_clean_tree_records_nothing() -> None:
    run = AnalysisRun.create(pass_id="t", version="0")
    assert record_partial_parse(run, "a.py", _tree(b"def f():\n    return 1\n")) == 0
    assert run.failed_files == []


def test_a_broken_tree_is_recorded_with_its_count() -> None:
    run = AnalysisRun.create(pass_id="t", version="0")
    count = record_partial_parse(run, "b.py", _tree(b"def f(:\n    return )\nclass\n"))
    assert count >= 1
    [entry] = run.failed_files
    assert entry["path"] == "b.py"
    assert entry["reason"].startswith(f"{PARTIAL_PARSE_REASON}: {count} damaged node(s)")


def test_a_tree_flagged_with_no_error_node_still_counts() -> None:
    """tree-sitter-markdown marks an empty pipe-table cell (``| y = x ||``)
    ``has_error`` and builds no ERROR or MISSING node anywhere, so a count of
    those nodes alone printed "0 ... node(s)" for a file it had just recorded
    as broken. The shape is crun's crun.1.md."""
    src = b"| a | b | c |\n|---|---|---|\n| x | y ||\n"
    tree = tree_sitter.Parser(get_language("markdown")).parse(src)
    assert tree.root_node.has_error
    run = AnalysisRun.create(pass_id="t", version="0")
    assert record_partial_parse(run, "t.md", tree) >= 1
    assert "0 damaged" not in run.failed_files[0]["reason"]
