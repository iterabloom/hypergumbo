# SPDX-License-Identifier: AGPL-3.0-or-later
"""``analyze.edge_target``: an edge endpoint is a node's id or a stand-in in the
id grammar, a file named by path is looked up, and the gate reports the rest.

The analyzer-level uses are tested in each analyzer's own package on the
production path. These pin the three helpers' contracts.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.analyze.base import (
    make_file_id,
    make_module_import_dst,
    make_unresolved_edge,
)
from hypergumbo_core.analyze.edge_target import (
    STAND_IN_SLOT_TOKENS,
    external_target,
    in_tree_file_id,
    non_id_edge_endpoints,
)
from hypergumbo_core.ir import Edge, ExternalRef, Span, Symbol, format_legacy_dst


def _sym(name: str) -> Symbol:
    return Symbol(
        id=f"fish:a.fish:1-2:{name}:function", name=name, kind="function",
        language="fish", path="a.fish",
        span=Span(start_line=1, end_line=2, start_col=0, end_col=1),
        origin="fish-v1",
    )


def _edge(src: str, dst: str) -> Edge:
    return Edge.create(
        src=src, dst=dst, edge_type="calls", line=1, origin="fish-v1",
        origin_run_id="run",
    )


def _importer(tmp_path: Path, rel_path: str) -> Path:
    path = tmp_path / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


def test_in_tree_file_id_is_the_first_candidate_on_disk(tmp_path: Path) -> None:
    importer = _importer(tmp_path, "conf/a.fish")
    _importer(tmp_path, "lib/util.fish")
    found = in_tree_file_id(
        importer, "conf/a.fish", ["conf/lib/util.fish", "lib/util.fish"],
    )
    assert found == make_file_id("fish", "lib/util.fish")


def test_in_tree_file_id_normalises_the_candidate(tmp_path: Path) -> None:
    importer = _importer(tmp_path, "conf/a.fish")
    _importer(tmp_path, "other.fish")
    assert in_tree_file_id(importer, "conf/a.fish", ["conf/../other.fish"]) == (
        make_file_id("fish", "other.fish")
    )


def test_in_tree_file_id_uses_the_target_files_own_language(tmp_path: Path) -> None:
    """A fish script sourcing a ``.sh`` file reaches the bash file node."""
    importer = _importer(tmp_path, "a.fish")
    _importer(tmp_path, "env.sh")
    assert in_tree_file_id(importer, "a.fish", ["env.sh"]) == make_file_id("bash", "env.sh")


def test_in_tree_file_id_misses_outside_absent_and_languageless(tmp_path: Path) -> None:
    importer = _importer(tmp_path / "repo", "a.fish")
    (tmp_path / "outside.fish").write_text("")
    _importer(tmp_path / "repo", "scene.tscn")
    misses = [
        "../outside.fish",   # leaves the repository
        "/etc/fish/x.fish",  # absolute
        ".", "..",           # a directory, not a file
        "absent.fish",       # not on disk
        "scene.tscn",        # on disk, but no analyzer gives it a node
    ]
    assert in_tree_file_id(importer, "a.fish", misses) is None


def test_external_target_with_a_module_matches_format_legacy_dst() -> None:
    dst, ref = external_target("starlark", "cc_binary", "@rules_cc//cc:defs.bzl")
    assert ref == ExternalRef(
        lang="starlark", module_path="@rules_cc//cc:defs.bzl", name="cc_binary",
    )
    assert dst == format_legacy_dst(ref)


def test_external_target_without_a_module_has_no_ref() -> None:
    dst, ref = external_target("cmake", "pthread")
    assert dst == "cmake:external:0-0:pthread:unresolved"
    assert ref is None


def test_external_target_keeps_a_colon_out_of_the_name_slot() -> None:
    dst, ref = external_target("cmake", "Qt5::Core", "pkg")
    assert dst == "cmake:pkg:0-0:Qt5..Core:unresolved"
    assert ref is not None and ref.name == "Qt5::Core"
    assert non_id_edge_endpoints([], [_edge(make_file_id("cmake", "a"), dst)]) == []


def test_gate_passes_symbols_file_anchors_and_helper_built_stand_ins() -> None:
    f, g = _sym("f"), _sym("g")
    file_id = make_file_id("fish", "a.fish")
    edges = [
        _edge(f.id, g.id),
        _edge(file_id, make_file_id("fish", "other.fish")),
        _edge(file_id, make_module_import_dst("fish", "/usr/share/x.fish")),
        _edge(f.id, external_target("fish", "x", "pkg")[0]),
        _edge(f.id, "fish:m:0-0:x:external_symbol"),
        make_unresolved_edge("fish", f.id, "echo", 1, "fish-v1", "run"),
    ]
    assert non_id_edge_endpoints([f, g], edges) == []


def test_gate_reports_each_endpoint_that_is_no_id() -> None:
    f = _sym("f")
    file_id = make_file_id("fish", "a.fish")
    bad_dsts = [
        "fish:other.fish:file",               # three slots
        "cmake:external:m:library",           # four slots
        "bare_name",                          # no colon at all
        "fish:a.fish:3-4:gone:function",      # a span, but no such symbol
        "fish:external:0-0:x:function",       # a stand-in of a non-stand-in kind
        "fish:external:0-0::unresolved",      # empty name slot
        "Fish:external:0-0:x:unresolved",     # language slot not lowercase
        "fish::0-0:x:unresolved",             # empty path slot
        "hack:unresolved:0-0:A::b:unresolved",  # colon in the name shifts the span
    ]
    edges = [_edge(file_id, dst) for dst in bad_dsts]
    assert non_id_edge_endpoints([f], edges) == [("dst", e) for e in edges]

    both = _edge("fish:a.fish:file:", "fish:other.fish:file")
    assert non_id_edge_endpoints([f], [both]) == [("src", both), ("dst", both)]


def test_gate_stand_in_tokens_are_the_callers_to_widen() -> None:
    edge = _edge(make_file_id("go", "a.go"), "go:fmt:0-0:package:package")
    assert non_id_edge_endpoints([], [edge]) == [("dst", edge)]
    assert non_id_edge_endpoints(
        [], [edge], stand_in_tokens=STAND_IN_SLOT_TOKENS | {"package"},
    ) == []
