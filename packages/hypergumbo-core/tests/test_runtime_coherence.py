# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the ADR-0023 §3 runtime coherence checker.

Exercise both the partition-finding logic and the CLI behavior. The
CLI is invoked through ``main(argv)`` directly (not as a subprocess)
so coverage flows through pytest-cov.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.runtime_coherence import (
    AllowlistEntry,
    PartitionOffender,
    filter_by_allowlist,
    find_offenders,
    format_report,
    load_allowlist,
    main,
)


# --- find_offenders ---


def _node(node_id: str, kind: str, language: str) -> dict:
    return {"id": node_id, "kind": kind, "language": language}


def _edge(src: str, dst: str, edge_type: str) -> dict:
    return {"src": src, "dst": dst, "type": edge_type}


def test_find_offenders_finds_partition_with_multiple_edge_types():
    bm = {
        "nodes": [
            _node("a", "function", "python"),
            _node("b", "type", "python"),
            _node("c", "function", "python"),
            _node("d", "type", "python"),
        ],
        "edges": [
            _edge("a", "b", "references"),
            _edge("c", "d", "type_ref"),
        ],
    }
    offenders = find_offenders(bm)
    assert len(offenders) == 1
    assert offenders[0].partition_key == (
        "function", "python", "type", "python",
    )
    assert offenders[0].edge_types == frozenset({"references", "type_ref"})
    assert offenders[0].edge_count == 2


def test_find_offenders_no_offenders_when_consistent():
    bm = {
        "nodes": [
            _node("a", "function", "python"),
            _node("b", "function", "python"),
        ],
        "edges": [
            _edge("a", "b", "calls"),
            _edge("a", "b", "calls"),
        ],
    }
    assert find_offenders(bm) == []


def test_find_offenders_skips_edges_with_unknown_endpoints():
    """Edges referencing IDs not in the node index are skipped silently."""
    bm = {
        "nodes": [_node("a", "function", "python")],
        "edges": [
            _edge("a", "missing", "calls"),
            _edge("missing", "a", "references"),
        ],
    }
    assert find_offenders(bm) == []


def test_find_offenders_skips_edges_with_empty_type():
    bm = {
        "nodes": [
            _node("a", "function", "python"),
            _node("b", "function", "python"),
        ],
        "edges": [
            _edge("a", "b", ""),
            _edge("a", "b", "calls"),
        ],
    }
    assert find_offenders(bm) == []


def test_find_offenders_handles_empty_input():
    assert find_offenders({}) == []
    assert find_offenders({"nodes": [], "edges": []}) == []


def test_find_offenders_orders_by_edge_count_descending():
    """Larger leaks come first; partition key breaks ties."""
    bm = {
        "nodes": [
            _node("a", "function", "python"),
            _node("b", "type", "python"),
            _node("c", "class", "python"),
            _node("d", "type", "python"),
        ],
        "edges": [
            _edge("a", "b", "references"),
            _edge("a", "b", "type_ref"),
            _edge("a", "b", "type_ref"),
            _edge("c", "d", "references"),
            _edge("c", "d", "type_ref"),
        ],
    }
    offenders = find_offenders(bm)
    assert len(offenders) == 2
    assert offenders[0].edge_count == 3
    assert offenders[1].edge_count == 2


def test_find_offenders_skips_nodes_without_id():
    """Malformed nodes (missing id) don't break the index."""
    bm = {
        "nodes": [
            {"kind": "function", "language": "python"},
            _node("a", "function", "python"),
            _node("b", "function", "python"),
        ],
        "edges": [_edge("a", "b", "calls")],
    }
    assert find_offenders(bm) == []


# --- load_allowlist ---


def test_load_allowlist_returns_empty_for_none(tmp_path: Path):
    assert load_allowlist(None) == []


def test_load_allowlist_returns_empty_for_missing_file(tmp_path: Path):
    assert load_allowlist(tmp_path / "nope.yaml") == []


def test_load_allowlist_returns_empty_for_empty_file(tmp_path: Path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    assert load_allowlist(p) == []


def test_load_allowlist_parses_yaml_entries(tmp_path: Path):
    p = tmp_path / "allow.yaml"
    p.write_text(
        "allowlist:\n"
        "  - src_kind: function\n"
        "    src_language: python\n"
        "    dst_kind: type\n"
        "    dst_language: python\n"
        "    permitted_edge_types: [references, type_ref]\n"
        "    rationale: A worked example.\n"
        "    adr_reference: ADR-0023 §X amendment\n"
    )
    entries = load_allowlist(p)
    assert len(entries) == 1
    assert entries[0].partition_key == (
        "function", "python", "type", "python",
    )
    assert entries[0].permitted_edge_types == frozenset(
        {"references", "type_ref"},
    )
    assert entries[0].rationale == "A worked example."
    assert entries[0].adr_reference == "ADR-0023 §X amendment"


def test_load_allowlist_handles_explicit_empty_list(tmp_path: Path):
    p = tmp_path / "empty-allow.yaml"
    p.write_text("allowlist: []\n")
    assert load_allowlist(p) == []


def test_load_allowlist_handles_null_allowlist(tmp_path: Path):
    """A YAML doc whose ``allowlist`` key is null must not crash."""
    p = tmp_path / "null-allow.yaml"
    p.write_text("allowlist:\n")
    assert load_allowlist(p) == []


def test_load_allowlist_uses_defaults_for_missing_fields(tmp_path: Path):
    p = tmp_path / "minimal.yaml"
    p.write_text(
        "allowlist:\n"
        "  - permitted_edge_types: [calls]\n"
    )
    entries = load_allowlist(p)
    assert len(entries) == 1
    assert entries[0].partition_key == ("", "", "", "")
    assert entries[0].permitted_edge_types == frozenset({"calls"})


# --- filter_by_allowlist ---


def _offender(
    src_kind: str, src_lang: str, dst_kind: str, dst_lang: str,
    edge_types: set[str], count: int = 1,
) -> PartitionOffender:
    return PartitionOffender(
        partition_key=(src_kind, src_lang, dst_kind, dst_lang),
        edge_types=frozenset(edge_types),
        edge_count=count,
    )


def _entry(
    src_kind: str, src_lang: str, dst_kind: str, dst_lang: str,
    permitted: set[str],
) -> AllowlistEntry:
    return AllowlistEntry(
        src_kind=src_kind, src_language=src_lang,
        dst_kind=dst_kind, dst_language=dst_lang,
        permitted_edge_types=frozenset(permitted),
    )


def test_filter_by_allowlist_removes_covered_offenders():
    offenders = [_offender("f", "py", "t", "py", {"references", "type_ref"})]
    allowlist = [_entry("f", "py", "t", "py", {"references", "type_ref"})]
    remaining, count = filter_by_allowlist(offenders, allowlist)
    assert remaining == []
    assert count == 1


def test_filter_by_allowlist_keeps_partially_covered_offenders():
    """Allow-list permits {A, B} but offender has {A, B, C}: still flagged."""
    offenders = [_offender("f", "py", "t", "py", {"a", "b", "c"})]
    allowlist = [_entry("f", "py", "t", "py", {"a", "b"})]
    remaining, count = filter_by_allowlist(offenders, allowlist)
    assert len(remaining) == 1
    assert count == 0


def test_filter_by_allowlist_keeps_uncovered_offenders():
    offenders = [_offender("f", "py", "t", "py", {"a"})]
    allowlist = [_entry("OTHER", "py", "t", "py", {"a"})]
    remaining, count = filter_by_allowlist(offenders, allowlist)
    assert len(remaining) == 1
    assert count == 0


def test_filter_by_allowlist_with_empty_allowlist():
    offenders = [_offender("f", "py", "t", "py", {"a", "b"})]
    remaining, count = filter_by_allowlist(offenders, [])
    assert remaining == offenders
    assert count == 0


# --- format_report ---


def test_format_report_clean():
    msg = format_report([])
    assert "No edge-type partition variance" in msg


def test_format_report_clean_with_edge_count():
    msg = format_report([], total_edges_scanned=42)
    assert "Scanned 42 edges." in msg


def test_format_report_with_offenders():
    offenders = [_offender("f", "py", "t", "py", {"a", "b"}, count=5)]
    msg = format_report(offenders)
    assert "1 un-allow-listed offender(s)" in msg
    assert "(f/py) -> (t/py)" in msg
    assert "['a', 'b']" in msg
    assert "5 edges" in msg


def test_format_report_all_allowlisted():
    msg = format_report([], allowlisted_count=3)
    assert "All 3 partition variance(s) are allow-listed." in msg


def test_format_report_offenders_plus_allowlisted():
    offenders = [_offender("f", "py", "t", "py", {"a", "b"})]
    msg = format_report(offenders, allowlisted_count=2)
    assert "1 un-allow-listed offender(s)" in msg
    assert "2 additional allow-listed" in msg


def test_format_report_renders_empty_partition_components():
    """Empty-string components render as <empty> for readability."""
    offenders = [_offender("", "py", "t", "", {"a", "b"})]
    msg = format_report(offenders)
    assert "(<empty>/py) -> (t/<empty>)" in msg


# --- CLI main() ---


def _write_bm(path: Path, behavior_map: dict) -> Path:
    path.write_text(json.dumps(behavior_map))
    return path


def test_cli_exit_0_on_clean(tmp_path: Path, capsys: pytest.CaptureFixture):
    bm_path = _write_bm(tmp_path / "bm.json", {
        "nodes": [
            _node("a", "function", "python"),
            _node("b", "function", "python"),
        ],
        "edges": [_edge("a", "b", "calls")],
    })
    rc = main([str(bm_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No edge-type partition variance" in out
    assert "Scanned 1 edges." in out


def test_cli_exit_1_on_offenders(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    bm_path = _write_bm(tmp_path / "bm.json", {
        "nodes": [
            _node("a", "function", "python"),
            _node("b", "type", "python"),
        ],
        "edges": [
            _edge("a", "b", "references"),
            _edge("a", "b", "type_ref"),
        ],
    })
    rc = main([str(bm_path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "1 un-allow-listed offender(s)" in out


def test_cli_exit_0_when_allowlist_covers_all_offenders(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    bm_path = _write_bm(tmp_path / "bm.json", {
        "nodes": [
            _node("a", "function", "python"),
            _node("b", "type", "python"),
        ],
        "edges": [
            _edge("a", "b", "references"),
            _edge("a", "b", "type_ref"),
        ],
    })
    allow_path = tmp_path / "allow.yaml"
    allow_path.write_text(
        "allowlist:\n"
        "  - src_kind: function\n"
        "    src_language: python\n"
        "    dst_kind: type\n"
        "    dst_language: python\n"
        "    permitted_edge_types: [references, type_ref]\n"
    )
    rc = main([str(bm_path), "--allowlist", str(allow_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "All 1 partition variance(s) are allow-listed" in out


def test_cli_exit_2_on_missing_input(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    rc = main([str(tmp_path / "does-not-exist.json")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "cannot read" in err


def test_cli_exit_2_on_invalid_json(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    rc = main([str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid JSON" in err
