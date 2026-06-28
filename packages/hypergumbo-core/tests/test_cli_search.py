# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the hypergumbo search command."""
import argparse
import json
from pathlib import Path

import pytest

from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.cli import (
    cmd_search,
    main,
    _positive_result_limit,
    _reject_unknown_choice,
)


class FakeArgs:
    """Minimal namespace for testing command functions."""

    pass


def test_cmd_search_finds_exact_match(tmp_path: Path, capsys) -> None:
    """Search finds symbols by exact name match."""
    # Create a behavior map with some symbols
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/utils.py:1-5:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/utils.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "foo"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "foo" in out
    assert "src/main.py" in out


def test_cmd_search_excludes_external_boundary_nodes(tmp_path: Path, capsys) -> None:
    """Search must skip synthetic boundary nodes (kind=external_symbol).

    Boundary nodes (created by ir.create_boundary_nodes for unresolved
    external edge endpoints — stdlib calls, third-party imports) have
    no source location and would surface as confusing "<external>"
    rows in default search output. They are first-class records in
    behavior_map['nodes'] now (PR1 of stop-stripping plan), so cmd_search
    must filter them via ir.is_external_boundary().
    """
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/fetch.py:1-3:fetch:function",
                "name": "fetch",
                "kind": "function",
                "language": "python",
                "path": "src/fetch.py",
                "span": {"start_line": 1, "end_line": 3, "start_col": 0, "end_col": 10},
            },
            {
                # Boundary node for the urllib.request.urlopen call inside fetch.
                "id": "python:urllib.request:0-0:urlopen:unresolved",
                "name": "urlopen",
                "kind": "external_symbol",
                "language": "python",
                "path": "<external>",
                "span": {"start_line": 0, "end_line": 0, "start_col": 0, "end_col": 0},
                "meta": {"external_boundary": True},
                "supply_chain": {"tier": 3, "tier_name": "external_dep",
                                 "reason": "unresolved external reference"},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "urlopen"  # would match the boundary node by name
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)
    assert result == 0

    out, _ = capsys.readouterr()
    # No symbols found — boundary node was filtered out.
    assert "No symbols found" in out
    assert "<external>" not in out


def test_cmd_search_fuzzy_match(tmp_path: Path, capsys) -> None:
    """Search finds symbols by fuzzy/partial match."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:getUserById:function",
                "name": "getUserById",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:6-10:getPostById:function",
                "name": "getPostById",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "ById"  # Partial match
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "getUserById" in out
    assert "getPostById" in out


def test_cmd_search_filter_by_kind(tmp_path: Path, capsys) -> None:
    """Search can filter by symbol kind."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:User:class",
                "name": "User",
                "kind": "class",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:6-10:user:function",
                "name": "user",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 6, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "user"
    args.path = str(tmp_path)
    args.input = None
    args.kind = "class"
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "User" in out
    assert "class" in out


def test_cmd_search_filter_by_language(tmp_path: Path, capsys) -> None:
    """Search can filter by language."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:getData:function",
                "name": "getData",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "javascript:src/main.js:1-5:getData:function",
                "name": "getData",
                "kind": "function",
                "language": "javascript",
                "path": "src/main.js",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "getData"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = "python"
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "python" in out
    assert "javascript" not in out


def test_cmd_search_no_results(tmp_path: Path, capsys) -> None:
    """Search reports no results when nothing matches."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:foo:function",
                "name": "foo",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "nonexistent"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "No symbols found" in out


def test_cmd_search_with_input_file(tmp_path: Path, capsys) -> None:
    """Search can read from specified input file."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:bar:function",
                "name": "bar",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    input_file = tmp_path / "custom_results.json"
    input_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "bar"
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "bar" in out


def test_cmd_search_input_not_found(tmp_path: Path) -> None:
    """Search fails if input file doesn't exist."""
    args = FakeArgs()
    args.pattern = "foo"
    args.path = str(tmp_path)
    args.input = str(tmp_path / "nonexistent.json")
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 1


def test_cmd_search_auto_runs_analysis(tmp_path: Path, capsys) -> None:
    """Search auto-runs analysis if no results file exists."""
    args = FakeArgs()
    args.pattern = "foo"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    # Auto-runs analysis and succeeds (even if no matches found)
    assert result == 0
    _, err = capsys.readouterr()
    assert "No cached results found, running analysis" in err


def test_cmd_search_respects_limit(tmp_path: Path, capsys) -> None:
    """Search respects the --limit option."""
    nodes = [
        {
            "id": f"python:src/file{i}.py:1-5:func{i}:function",
            "name": f"func{i}",
            "kind": "function",
            "language": "python",
            "path": f"src/file{i}.py",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        }
        for i in range(10)
    ]
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "func"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 3

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show only 3 results
    assert out.count("function") <= 3


def test_cmd_search_header_reports_total_not_post_limit(
    tmp_path: Path, capsys
) -> None:
    """INV-toniv: the header reports the TOTAL number of matches, not the
    post-limit count, and discloses how many are shown when truncated."""
    nodes = [
        {
            "id": f"python:src/f{i}.py:1-5:func{i}:function",
            "name": f"func{i}", "kind": "function", "language": "python",
            "path": f"src/f{i}.py",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        }
        for i in range(10)
    ]
    behavior_map = {"schema_version": SCHEMA_VERSION, "nodes": nodes, "edges": []}
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "func"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 3

    assert cmd_search(args) == 0
    out, _ = capsys.readouterr()
    assert "Found 10 symbol(s)" in out, "header must report the total (10), not 3"
    assert "showing 3" in out, "truncation must be disclosed"


def test_cmd_search_header_no_showing_qualifier_when_under_limit(
    tmp_path: Path, capsys
) -> None:
    """When matches <= limit the header omits the '(showing …)' qualifier."""
    nodes = [
        {
            "id": f"python:src/f{i}.py:1-5:func{i}:function",
            "name": f"func{i}", "kind": "function", "language": "python",
            "path": f"src/f{i}.py",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        }
        for i in range(2)
    ]
    behavior_map = {"schema_version": SCHEMA_VERSION, "nodes": nodes, "edges": []}
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "func"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 20

    assert cmd_search(args) == 0
    out, _ = capsys.readouterr()
    assert "Found 2 symbol(s) matching 'func':" in out
    assert "showing" not in out


def test_positive_result_limit_type_factory() -> None:
    """INV-toniv: --limit type factory accepts positives, rejects <=0 and junk."""
    assert _positive_result_limit("5") == 5
    assert _positive_result_limit("1") == 1
    for bad in ("0", "-1", "-5"):
        with pytest.raises(argparse.ArgumentTypeError):
            _positive_result_limit(bad)
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_result_limit("abc")


def test_main_search_negative_limit_rejected(tmp_path: Path, capsys) -> None:
    """INV-toniv: a negative --limit is rejected at the CLI (exit 2), not
    silently interpreted as Python tail-drop slicing."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:test:function", "name": "test",
                "kind": "function", "language": "python", "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

    with pytest.raises(SystemExit) as exc:
        main(["search", "test", "--path", str(tmp_path), "--limit", "-5"])
    assert exc.value.code == 2
    _, err = capsys.readouterr()
    assert "limit" in err.lower()


def test_main_with_search(tmp_path: Path, capsys) -> None:
    """Main with search command."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:test:function",
                "name": "test",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    result = main(["search", "test", "--path", str(tmp_path)])

    assert result == 0

    out, _ = capsys.readouterr()
    assert "test" in out


def test_cmd_search_prints_output_summary(tmp_path: Path, capsys) -> None:
    """Search prints output summary to stdout."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:test:function",
                "name": "test",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.pattern = "test"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = None

    result = cmd_search(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # With auto-discovery, uses cached results
    assert "[hypergumbo search] Using 1 cached" in out
    assert "Output: stdout" in out


# --- WI-furop: CLI filter-value validation (INV-fabov family) ---


def test_reject_unknown_choice_accepts_valid() -> None:
    """A value in the enumerable set returns None (no error)."""
    valid = frozenset({"python", "java"})
    assert _reject_unknown_choice(
        "python", valid, subcommand="search", noun="language"
    ) is None


def test_reject_unknown_choice_rejects_with_suggestion(capsys) -> None:
    """A near-miss is rejected (rc=2) with a did-you-mean suggestion."""
    valid = frozenset({"python", "java"})
    rc = _reject_unknown_choice(
        "pythn", valid, subcommand="search", noun="language"
    )
    assert rc == 2
    _, err = capsys.readouterr()
    assert "is not a known language" in err
    assert "Did you mean: python" in err


def test_reject_unknown_choice_rejects_without_suggestion(capsys) -> None:
    """A value with no close match is rejected (rc=2), no suggestion line."""
    valid = frozenset({"python", "java"})
    rc = _reject_unknown_choice(
        "zzzzzzzz", valid, subcommand="search", noun="language"
    )
    assert rc == 2
    _, err = capsys.readouterr()
    assert "is not a known language" in err
    assert "Did you mean" not in err


def test_cmd_search_rejects_unknown_language(tmp_path: Path, capsys) -> None:
    """WI-furop: --language with a non-language errors (exit 2), not silent."""
    args = FakeArgs()
    args.pattern = "main"
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = "klingon"
    args.limit = 20

    result = cmd_search(args)

    assert result == 2
    _, err = capsys.readouterr()
    assert "is not a known language" in err


def test_cmd_search_rejects_unknown_kind(tmp_path: Path, capsys) -> None:
    """WI-furop: --kind with an unregistered kind errors (exit 2)."""
    args = FakeArgs()
    args.pattern = "main"
    args.path = str(tmp_path)
    args.input = None
    args.kind = "nonexistent_kind"
    args.language = None
    args.limit = 20

    result = cmd_search(args)

    assert result == 2
    _, err = capsys.readouterr()
    assert "is not a known symbol kind" in err
