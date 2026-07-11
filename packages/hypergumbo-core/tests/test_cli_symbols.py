# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the hypergumbo symbols command."""
import json
import re
from pathlib import Path

from hypergumbo_core.schema import SCHEMA_VERSION
from hypergumbo_core.cli import cmd_symbols, main, _symbols_column_config

# SGR (color/style) ANSI escape sequences. Rich emits these when color is
# enabled — which happens even for a non-tty capsys capture when the ambient
# environment sets FORCE_COLOR (as this dev environment does). The codes
# interleave with rendered text (e.g. a styled count `40` is separated from
# ` additional…` by a reset code), which breaks any assertion that treats the
# rendered output as contiguous text. Stripping them makes text assertions
# hermetic w.r.t. the ambient color environment (WI-sapaj).
_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove SGR ANSI style codes so text assertions don't depend on color."""
    return _ANSI_SGR.sub("", text)


class FakeArgs:
    """Minimal namespace for testing command functions."""

    pass


def test_cmd_symbols_shows_tabular_output(tmp_path: Path, capsys) -> None:
    """Symbols command shows tabular output with degrees."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:11-20:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 11, "end_line": 20, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            {
                "id": "edge:1",
                "src": "python:src/main.py:1-10:main:function",
                "dst": "python:src/main.py:11-20:helper:function",
                "type": "calls",
                "line": 5,
                "confidence": 0.9,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Check table headers
    assert "Symbol" in out
    assert "Kind" in out
    assert "In" in out
    assert "Out" in out
    assert "Deg" in out
    assert "File" in out
    # Check data
    assert "main" in out
    assert "helper" in out
    assert "function" in out
    assert "src/main.py" in out


def test_cmd_symbols_kind_column_not_truncated(tmp_path: Path, capsys) -> None:
    """WI-puroz: Kind column must show full kind names without ellipsis."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:main:function",
                "name": "main",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:11-20:MyClass:class",
                "name": "MyClass",
                "kind": "class",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 11, "end_line": 20, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:21-30:helper:method",
                "name": "helper",
                "kind": "method",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 21, "end_line": 30, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)
    assert result == 0

    out, _ = capsys.readouterr()
    assert "function" in out
    assert "class" in out
    assert "method" in out
    # WI-puroz: no ellipsis in kind column
    assert "…" not in out, "Kind column must not contain ellipsis (…)"
    assert "functi…" not in out


def test_cmd_symbols_numeric_columns_not_truncated(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """INV-ripoh: the In/Out/Deg columns must render full multi-digit values
    (no ellipsis) on a default ~120-col terminal. A connectivity hub with a
    4-digit in-degree previously rendered Deg as '10…', destroying the rank."""
    import os
    import hypergumbo_core.cli as cli_mod

    # Force the default-terminal path deterministically (no tty in tests).
    monkeypatch.setattr(
        cli_mod.shutil, "get_terminal_size",
        lambda *a, **k: os.terminal_size((120, 24)),
    )

    hub = "python:src/hub.py:1-9:Hub:function"
    src = "python:src/src.py:1-9:Src:function"
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": hub, "name": "Hub", "kind": "function", "language": "python",
                "path": "src/hub.py",
                "span": {"start_line": 1, "end_line": 9, "start_col": 0, "end_col": 0},
            },
            {
                "id": src, "name": "Src", "kind": "function", "language": "python",
                "path": "src/src.py",
                "span": {"start_line": 1, "end_line": 9, "start_col": 0, "end_col": 0},
            },
        ],
        # 1000 distinct edges Src -> Hub => Hub in-degree 1000 (4 digits),
        # Src out-degree 1000, both Deg=1000.
        "edges": [
            {
                "id": f"edge:{i}", "src": src, "dst": hub, "type": "calls",
                "line": 1, "confidence": 0.9,
            }
            for i in range(1000)
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)
    assert result == 0

    out, _ = capsys.readouterr()
    # The full 4-digit value must render (not "10…").
    assert "1000" in out, "full 4-digit degree value must render"
    # No column may be ellipsis-truncated (Symbol/File names here are short).
    assert "…" not in out, "numeric columns must not be ellipsis-truncated"


def test_cmd_symbols_sorts_by_individual_degree(tmp_path: Path, capsys) -> None:
    """Symbols are sorted by bidirectional centrality, not file total.

    Regression test for the Django/NestJS bakeoff finding: a symbol with the
    highest individual degree (e.g. QuerySet, degree=118) was buried at line 753
    because expressions.py had higher *file total* degree. Sorting by bidirectional
    centrality ``in_degree * (1 + ln(1 + out_degree))`` ensures the most connected
    symbols always appear first.
    """
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            # "expressions.py" — many symbols with moderate degree
            {
                "id": "python:src/expressions.py:1-10:Func:class",
                "name": "Func",
                "kind": "class",
                "language": "python",
                "path": "src/expressions.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/expressions.py:11-20:Value:class",
                "name": "Value",
                "kind": "class",
                "language": "python",
                "path": "src/expressions.py",
                "span": {"start_line": 11, "end_line": 20, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/expressions.py:21-30:Expr:class",
                "name": "Expr",
                "kind": "class",
                "language": "python",
                "path": "src/expressions.py",
                "span": {"start_line": 21, "end_line": 30, "start_col": 0, "end_col": 10},
            },
            # "query.py" — one symbol with the HIGHEST individual degree
            {
                "id": "python:src/query.py:1-100:QuerySet:class",
                "name": "QuerySet",
                "kind": "class",
                "language": "python",
                "path": "src/query.py",
                "span": {"start_line": 1, "end_line": 100, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            # Internal edges within expressions.py:
            # Func→Value, Value→Expr, Expr→Func (circular deps)
            # Each symbol gets degree 2, file total = 6
            {"id": "edge:1", "src": "python:src/expressions.py:1-10:Func:class",
             "dst": "python:src/expressions.py:11-20:Value:class",
             "type": "calls", "line": 5, "confidence": 0.9},
            {"id": "edge:2", "src": "python:src/expressions.py:11-20:Value:class",
             "dst": "python:src/expressions.py:21-30:Expr:class",
             "type": "calls", "line": 15, "confidence": 0.9},
            {"id": "edge:3", "src": "python:src/expressions.py:21-30:Expr:class",
             "dst": "python:src/expressions.py:1-10:Func:class",
             "type": "calls", "line": 25, "confidence": 0.9},
            # QuerySet calls all three + each also calls back into QuerySet
            # QuerySet: out=3, in=3 → degree=6
            # Func: in=2(Expr+QS), out=2(Value+QS) → degree=4
            # Value: in=2(Func+QS), out=2(Expr+QS) → degree=4
            # Expr: in=2(Value+QS), out=2(Func+QS) → degree=4
            # File totals: expressions.py = 12, query.py = 6
            # With file-total sort: Func first (12 > 6)
            # With individual sort: QuerySet first (6 > 4)
            {"id": "edge:4", "src": "python:src/query.py:1-100:QuerySet:class",
             "dst": "python:src/expressions.py:1-10:Func:class",
             "type": "calls", "line": 10, "confidence": 0.9},
            {"id": "edge:5", "src": "python:src/query.py:1-100:QuerySet:class",
             "dst": "python:src/expressions.py:11-20:Value:class",
             "type": "calls", "line": 20, "confidence": 0.9},
            {"id": "edge:6", "src": "python:src/query.py:1-100:QuerySet:class",
             "dst": "python:src/expressions.py:21-30:Expr:class",
             "type": "calls", "line": 30, "confidence": 0.9},
            {"id": "edge:7", "src": "python:src/expressions.py:1-10:Func:class",
             "dst": "python:src/query.py:1-100:QuerySet:class",
             "type": "calls", "line": 6, "confidence": 0.9},
            {"id": "edge:8", "src": "python:src/expressions.py:11-20:Value:class",
             "dst": "python:src/query.py:1-100:QuerySet:class",
             "type": "calls", "line": 16, "confidence": 0.9},
            {"id": "edge:9", "src": "python:src/expressions.py:21-30:Expr:class",
             "dst": "python:src/query.py:1-100:QuerySet:class",
             "type": "calls", "line": 26, "confidence": 0.9},
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # QuerySet (degree=6) should appear BEFORE Func (degree=4) even though
    # expressions.py has higher total file degree (12 vs 6)
    queryset_pos = out.find("QuerySet")
    func_pos = out.find("Func")
    assert queryset_pos < func_pos, (
        f"QuerySet (highest individual degree=6) should appear before Func (degree=4), "
        f"but found QuerySet at {queryset_pos}, Func at {func_pos}"
    )


def test_cmd_symbols_connector_outranks_pure_sink(tmp_path: Path, capsys) -> None:
    """Bidirectional centrality: connector (in+out) ranks above pure sink (in only).

    A pure sink (high in-degree, zero out-degree) like an exception class should
    rank below a connector (moderate in-degree, moderate out-degree) like a
    service function.  Uses ``in_degree * (1 + ln(1 + out_degree))``.

    Pure sink:  in=8, out=0 → 8 * (1 + ln(1)) = 8 * 1 = 8.0
    Connector:  in=5, out=6 → 5 * (1 + ln(7)) = 5 * 2.946 = 14.7
    """
    nodes = [
        {
            "id": "python:src/exc.py:1-10:ValidationError:class",
            "name": "ValidationError",
            "kind": "class",
            "language": "python",
            "path": "src/exc.py",
            "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
        },
        {
            "id": "python:src/svc.py:1-50:UserService:class",
            "name": "UserService",
            "kind": "class",
            "language": "python",
            "path": "src/svc.py",
            "span": {"start_line": 1, "end_line": 50, "start_col": 0, "end_col": 10},
        },
    ]
    # Create callers that import into ValidationError (pure sink)
    callers = []
    for i in range(8):
        caller_id = f"python:src/caller{i}.py:1-5:caller{i}:function"
        nodes.append({
            "id": caller_id,
            "name": f"caller{i}",
            "kind": "function",
            "language": "python",
            "path": f"src/caller{i}.py",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        })
        callers.append(caller_id)

    edges = []
    edge_id = 1

    # 8 callers → ValidationError (pure sink: in=8, out=0)
    for caller_id in callers:
        edges.append({
            "id": f"edge:{edge_id}",
            "src": caller_id,
            "dst": "python:src/exc.py:1-10:ValidationError:class",
            "type": "calls", "line": 3, "confidence": 0.9,
        })
        edge_id += 1

    # UserService: in=5 (callers 0-4 call it), out=6 (calls callers 2-7)
    for caller_id in callers[:5]:
        edges.append({
            "id": f"edge:{edge_id}",
            "src": caller_id,
            "dst": "python:src/svc.py:1-50:UserService:class",
            "type": "calls", "line": 2, "confidence": 0.9,
        })
        edge_id += 1
    for caller_id in callers[2:]:
        edges.append({
            "id": f"edge:{edge_id}",
            "src": "python:src/svc.py:1-50:UserService:class",
            "dst": caller_id,
            "type": "calls", "line": 10, "confidence": 0.9,
        })
        edge_id += 1

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": edges,
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)
    assert result == 0

    out, _ = capsys.readouterr()
    svc_pos = out.find("UserService")
    err_pos = out.find("ValidationError")
    assert svc_pos < err_pos, (
        f"UserService (connector, in=5 out=6) should rank above "
        f"ValidationError (pure sink, in=8 out=0) with bidirectional centrality, "
        f"but found UserService at {svc_pos}, ValidationError at {err_pos}"
    )


def test_cmd_symbols_truncates_with_message(tmp_path: Path, capsys) -> None:
    """Symbols truncates at --limit and shows message."""
    # Create many symbols
    nodes = []
    for i in range(50):
        nodes.append({
            "id": f"python:src/file{i}.py:1-5:func{i}:function",
            "name": f"func{i}",
            "kind": "function",
            "language": "python",
            "path": f"src/file{i}.py",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        })

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 10  # Only show 10
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    out = _strip_ansi(out)
    # Should show truncation message
    assert "40 additional symbols omitted for brevity" in out
    assert "--all" in out


def test_cmd_symbols_all_flag(tmp_path: Path, capsys) -> None:
    """--all flag shows all symbols regardless of limit."""
    nodes = []
    for i in range(50):
        nodes.append({
            "id": f"python:src/file{i}.py:1-5:func{i}:function",
            "name": f"func{i}",
            "kind": "function",
            "language": "python",
            "path": f"src/file{i}.py",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        })

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 10  # Would truncate normally
    args.all = True  # But --all overrides
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should NOT show truncation message
    assert "additional symbols omitted" not in out
    # Should show all 50 symbols
    assert "func49" in out


def test_cmd_symbols_filter_by_kind(tmp_path: Path, capsys) -> None:
    """Symbols can be filtered by kind."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:MyClass:class",
                "name": "MyClass",
                "kind": "class",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:11-15:my_func:function",
                "name": "my_func",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 11, "end_line": 15, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = "function"
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "my_func" in out
    assert "MyClass" not in out


def test_cmd_symbols_filter_by_language(tmp_path: Path, capsys) -> None:
    """Symbols can be filtered by language."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:py_func:function",
                "name": "py_func",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "javascript:src/main.js:1-10:jsFunc:function",
                "name": "jsFunc",
                "kind": "function",
                "language": "javascript",
                "path": "src/main.js",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = "python"
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "py_func" in out
    assert "jsFunc" not in out


def test_cmd_symbols_no_symbols_found(tmp_path: Path, capsys) -> None:
    """Symbols command handles no symbols gracefully."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "No symbols found" in out


def test_cmd_symbols_with_input_file(tmp_path: Path, capsys) -> None:
    """Symbols can read from specified input file."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:custom_func:function",
                "name": "custom_func",
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
    args.path = str(tmp_path)
    args.input = str(input_file)
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "custom_func" in out


def test_cmd_symbols_input_not_found(tmp_path: Path) -> None:
    """Symbols fails if input file doesn't exist."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = str(tmp_path / "nonexistent.json")
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 1


def test_main_wrong_shape_input_exits_2_via_dispatch_guard(
    tmp_path: Path, capsys,
) -> None:
    """A wrong-shape ``--input`` (no ``nodes``) surfaces through ``main()``'s
    dispatch as a clean ``rc=2`` + stderr message (WI-jukah / INV-sozop), not
    a raw traceback or a silent ``rc=0`` "No symbols found". Exercises the
    top-level ``except SubstrateError`` guard end-to-end."""
    bad = tmp_path / "wrongshape.json"
    bad.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "no_nodes": 1}))

    rc = main(["symbols", str(tmp_path), "--input", str(bad)])

    assert rc == 2
    err = capsys.readouterr().err
    assert "Error" in err
    assert "nodes" in err


def test_main_non_directory_path_exits_2(tmp_path: Path, capsys) -> None:
    """INV-jibof: a positional path that is a non-directory file (not a repo,
    no --input, no cached results) fails with rc=2 + guidance instead of
    silently running analysis on a non-repo and printing "No symbols found"
    with rc=0. Exercises the _get_or_run_analysis directory guard end-to-end."""
    notadir = tmp_path / "notadir.txt"
    notadir.write_text("hello\n")

    rc = main(["symbols", str(notadir)])

    assert rc == 2
    assert "not a directory" in capsys.readouterr().err


def test_cmd_symbols_auto_runs_analysis(tmp_path: Path, capsys) -> None:
    """Symbols auto-runs analysis if no results file exists."""
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    # Auto-runs analysis and succeeds (even if no symbols found)
    assert result == 0
    _, err = capsys.readouterr()
    assert "No cached results found, running analysis" in err


def test_cmd_symbols_prints_output_summary(tmp_path: Path, capsys) -> None:
    """Symbols prints output summary to stdout."""
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
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    assert "[hypergumbo symbols] Using 1 cached" in out
    assert "Output: stdout" in out


def test_cmd_symbols_filter_by_kind_and_language(tmp_path: Path, capsys) -> None:
    """Symbols can be filtered by both kind and language."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:py_func:function",
                "name": "py_func",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:11-20:PyClass:class",
                "name": "PyClass",
                "kind": "class",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 11, "end_line": 20, "start_col": 0, "end_col": 10},
            },
            {
                "id": "javascript:src/main.js:1-10:jsFunc:function",
                "name": "jsFunc",
                "kind": "function",
                "language": "javascript",
                "path": "src/main.js",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = "function"  # Filter to functions only
    args.language = "python"  # Filter to python only
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show Python function only
    assert "py_func" in out
    # Should not show Python class (wrong kind)
    assert "PyClass" not in out
    # Should not show JS function (wrong language)
    assert "jsFunc" not in out


def test_cmd_symbols_exclude_tests(tmp_path: Path, capsys) -> None:
    """--exclude-tests flag filters out test symbols."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:main_func:function",
                "name": "main_func",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:tests/test_main.py:1-10:test_main:function",
                "name": "test_main",
                "kind": "function",
                "language": "python",
                "path": "tests/test_main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:test_utils.py:1-10:test_helper:function",
                "name": "test_helper",
                "kind": "function",
                "language": "python",
                "path": "test_utils.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = True  # Exclude tests
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show non-test symbol
    assert "main_func" in out
    # Should not show test symbols
    assert "test_main" not in out
    assert "test_helper" not in out


def test_cmd_symbols_max_per_file(tmp_path: Path, capsys) -> None:
    """--max-per-file limits symbols shown per file."""
    # Create multiple symbols in same file
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": f"python:src/hot.py:{i}-{i+5}:func{i}:function",
                "name": f"func{i}",
                "kind": "function",
                "language": "python",
                "path": "src/hot.py",
                "span": {"start_line": i, "end_line": i+5, "start_col": 0, "end_col": 10},
            }
            for i in range(10)
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = 3  # Only 3 per file

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Should show some funcs from hot.py
    assert "func" in out
    # Count occurrences - should be limited to 3
    count = sum(1 for i in range(10) if f"func{i}" in out)
    assert count == 3, f"Expected 3 symbols from hot.py, got {count}"


def test_cmd_symbols_max_per_file_with_all(tmp_path: Path, capsys) -> None:
    """--max-per-file with --all shows all files but limited symbols per file."""
    # Create symbols in multiple files
    nodes = []
    for file_idx in range(5):
        for sym_idx in range(10):
            nodes.append({
                "id": f"python:src/file{file_idx}.py:{sym_idx}-{sym_idx+5}:func{file_idx}_{sym_idx}:function",
                "name": f"func{file_idx}_{sym_idx}",
                "kind": "function",
                "language": "python",
                "path": f"src/file{file_idx}.py",
                "span": {"start_line": sym_idx, "end_line": sym_idx+5, "start_col": 0, "end_col": 10},
            })

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200  # Would normally truncate to 200
    args.all = True   # But --all ignores this
    args.exclude_tests = False
    args.max_per_file = 2  # Limit to 2 per file

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # All 5 files should be represented
    for file_idx in range(5):
        assert f"file{file_idx}.py" in out
    # Each file should have max 2 symbols
    # Total should be 5 files * 2 symbols = 10 symbols
    assert "additional symbols omitted" not in out  # --all with max-per-file


def test_cmd_symbols_exclude_tests_affects_degree_counts(tmp_path: Path, capsys) -> None:
    """--exclude-tests excludes test edges from degree counts, not just display."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:main_func:function",
                "name": "main_func",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:src/main.py:11-20:helper:function",
                "name": "helper",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 11, "end_line": 20, "start_col": 0, "end_col": 10},
            },
            {
                "id": "python:tests/test_main.py:1-10:test_main:function",
                "name": "test_main",
                "kind": "function",
                "language": "python",
                "path": "tests/test_main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            # test_main calls main_func (should be excluded from degree when -x)
            {
                "id": "edge:1",
                "src": "python:tests/test_main.py:1-10:test_main:function",
                "dst": "python:src/main.py:1-10:main_func:function",
                "type": "calls",
                "line": 5,
                "confidence": 0.9,
            },
            # main_func calls helper (non-test edge, should always be counted)
            {
                "id": "edge:2",
                "src": "python:src/main.py:1-10:main_func:function",
                "dst": "python:src/main.py:11-20:helper:function",
                "type": "calls",
                "line": 8,
                "confidence": 0.9,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    # First, run WITHOUT exclude_tests to see baseline degrees
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    cmd_symbols(args)
    out_without_exclude, _ = capsys.readouterr()

    # main_func should have in-degree=1 (from test_main) without exclude
    # The output shows: name, kind, in, out, deg, file
    # Find the line with main_func and check its in-degree
    for line in out_without_exclude.split("\n"):
        if "main_func" in line:
            # In the table, columns are: Symbol, Kind, In, Out, Deg, File
            # The "In" column should show 1 (from test_main calling it)
            parts = line.split()
            # We need to find the In value - it's the 3rd column after Symbol and Kind
            # But parsing Rich table output is tricky; let's just check "1" appears
            assert "1" in line, f"main_func should have in-degree 1: {line}"
            break

    # Now run WITH exclude_tests
    args.exclude_tests = True
    cmd_symbols(args)
    out_with_exclude, _ = capsys.readouterr()

    # main_func should have in-degree=0 (test edge excluded)
    # helper should have in-degree=1 (from main_func, non-test edge)
    for line in out_with_exclude.split("\n"):
        if "main_func" in line:
            # main_func: in=0, out=1 (calls helper)
            # The line should show "0" for in-degree (first number after "function")
            # Let's check that the pattern shows 0 for in-degree
            parts = line.split()
            # Find index of "function" then next should be in-degree
            if "function" in parts:
                idx = parts.index("function")
                in_deg = parts[idx + 1] if idx + 1 < len(parts) else None
                assert in_deg == "0", f"main_func should have in-degree 0 with -x: {line}"
            break


def test_cmd_symbols_filters_low_confidence_edges(tmp_path: Path, capsys) -> None:
    """Low-confidence inferred edges are excluded from degree computation.

    Simulates the DirLocker.Lock scenario: a method has 5 low-confidence
    inferred edges (0.3) from unrelated .Lock() calls plus 1 real high-confidence
    edge (0.9). Without filtering, Lock appears with in-degree 6.
    With confidence filtering (default 0.5), Lock should show in-degree 1.
    """
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "go:src/locker.go:1-10:DirLocker.Lock:method",
                "name": "DirLocker.Lock",
                "kind": "method",
                "language": "go",
                "path": "src/locker.go",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "go:src/db.go:1-10:DB.Open:method",
                "name": "DB.Open",
                "kind": "method",
                "language": "go",
                "path": "src/db.go",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "go:src/a.go:1-5:funcA:function",
                "name": "funcA",
                "kind": "function",
                "language": "go",
                "path": "src/a.go",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "go:src/b.go:1-5:funcB:function",
                "name": "funcB",
                "kind": "function",
                "language": "go",
                "path": "src/b.go",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "go:src/c.go:1-5:funcC:function",
                "name": "funcC",
                "kind": "function",
                "language": "go",
                "path": "src/c.go",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "go:src/d.go:1-5:funcD:function",
                "name": "funcD",
                "kind": "function",
                "language": "go",
                "path": "src/d.go",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "go:src/e.go:1-5:funcE:function",
                "name": "funcE",
                "kind": "function",
                "language": "go",
                "path": "src/e.go",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [
            # 1 real call to DirLocker.Lock (high confidence)
            {
                "id": "edge:real",
                "src": "go:src/db.go:1-10:DB.Open:method",
                "dst": "go:src/locker.go:1-10:DirLocker.Lock:method",
                "type": "calls",
                "confidence": 0.9,
            },
            # 5 false-positive inferred edges from unrelated .Lock() calls
            {
                "id": "edge:false1",
                "src": "go:src/a.go:1-5:funcA:function",
                "dst": "go:src/locker.go:1-10:DirLocker.Lock:method",
                "type": "calls",
                "confidence": 0.3,
            },
            {
                "id": "edge:false2",
                "src": "go:src/b.go:1-5:funcB:function",
                "dst": "go:src/locker.go:1-10:DirLocker.Lock:method",
                "type": "calls",
                "confidence": 0.3,
            },
            {
                "id": "edge:false3",
                "src": "go:src/c.go:1-5:funcC:function",
                "dst": "go:src/locker.go:1-10:DirLocker.Lock:method",
                "type": "calls",
                "confidence": 0.3,
            },
            {
                "id": "edge:false4",
                "src": "go:src/d.go:1-5:funcD:function",
                "dst": "go:src/locker.go:1-10:DirLocker.Lock:method",
                "type": "calls",
                "confidence": 0.3,
            },
            {
                "id": "edge:false5",
                "src": "go:src/e.go:1-5:funcE:function",
                "dst": "go:src/locker.go:1-10:DirLocker.Lock:method",
                "type": "calls",
                "confidence": 0.3,
            },
        ],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    cmd_symbols(args)
    out, _ = capsys.readouterr()

    # DirLocker.Lock should have in-degree=1 (only the real 0.9 edge)
    # Low-confidence edges (0.3) should be filtered out
    for line in out.split("\n"):
        if "DirLocker.Lock" in line:
            parts = line.split()
            if "method" in parts:
                idx = parts.index("method")
                in_deg = parts[idx + 1] if idx + 1 < len(parts) else None
                assert in_deg == "1", (
                    f"DirLocker.Lock should have in-degree 1 after confidence "
                    f"filtering, got: {line}"
                )
            break
    else:
        # DirLocker.Lock should appear in the output
        raise AssertionError(f"DirLocker.Lock not found in output: {out}")


def test_cmd_symbols_excludes_excluded_kinds(tmp_path: Path, capsys) -> None:
    """Symbols command filters out EXCLUDED_KINDS (CSS variables, etc.)."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:main_func:function",
                "name": "main_func",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "css:src/styles.css:1-5:--main-color:variable",
                "name": "--main-color",
                "kind": "variable",
                "language": "css",
                "path": "src/styles.css",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
            {
                "id": "css:src/styles.css:10-15:fadeIn:keyframes",
                "name": "fadeIn",
                "kind": "keyframes",
                "language": "css",
                "path": "src/styles.css",
                "span": {"start_line": 10, "end_line": 15, "start_col": 0, "end_col": 10},
            },
            {
                "id": "css:src/styles.css:20-25:mobile:media",
                "name": "mobile",
                "kind": "media",
                "language": "css",
                "path": "src/styles.css",
                "span": {"start_line": 20, "end_line": 25, "start_col": 0, "end_col": 10},
            },
            {
                "id": "css:src/styles.css:30-35:CustomFont:font_face",
                "name": "CustomFont",
                "kind": "font_face",
                "language": "css",
                "path": "src/styles.css",
                "span": {"start_line": 30, "end_line": 35, "start_col": 0, "end_col": 10},
            },
            {
                "id": "css:src/styles.css:40-45:.header:class_selector",
                "name": ".header",
                "kind": "class_selector",
                "language": "css",
                "path": "src/styles.css",
                "span": {"start_line": 40, "end_line": 45, "start_col": 0, "end_col": 10},
            },
            {
                # Post-Phase-4b (ADR-0027 §6, PR #3633): npm packages
                # are emitted as kind=package + meta.package_ecosystem.
                "id": "javascript:npm:vue:package",
                "name": "vue",
                "kind": "package",
                "language": "javascript",
                "path": "",
                "span": {"start_line": 0, "end_line": 0, "start_col": 0, "end_col": 0},
                "meta": {"package_ecosystem": "npm"},
            },
            {
                # Post-Phase-4b: synthetic module-file nodes are emitted
                # as kind=file + meta.module_system.
                "id": "javascript:src/utils.js:file:1:utils",
                "name": "utils",
                "kind": "file",
                "language": "javascript",
                "path": "src/utils.js",
                "span": {"start_line": 0, "end_line": 0, "start_col": 0, "end_col": 0},
                "meta": {"module_system": "esm"},
            },
        ],
        "edges": [],
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)

    assert result == 0

    out, _ = capsys.readouterr()
    # Production function should be visible
    assert "main_func" in out
    # All excluded CSS kinds should be filtered out
    assert "--main-color" not in out
    assert "fadeIn" not in out
    assert "mobile" not in out
    assert "CustomFont" not in out
    assert ".header" not in out
    # npm_package and module_file should be filtered out
    assert "vue" not in out
    assert "utils" not in out


def _excluded_kind_map() -> dict:
    """A function plus two default-excluded kinds (file, CSS variable)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-10:main_func:function",
                "name": "main_func",
                "kind": "function",
                "language": "python",
                "path": "src/main.py",
                "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 10},
            },
            {
                "id": "javascript:src/utils.js:file:1:utils_file",
                "name": "utils_file",
                "kind": "file",
                "language": "javascript",
                "path": "src/utils.js",
                "span": {"start_line": 0, "end_line": 0, "start_col": 0, "end_col": 0},
                "meta": {"module_system": "esm"},
            },
            {
                "id": "css:src/styles.css:1-5:--brand-color:variable",
                "name": "--brand-color",
                "kind": "variable",
                "language": "css",
                "path": "src/styles.css",
                "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
            },
        ],
        "edges": [],
    }


def _symbols_args(tmp_path: Path, **overrides) -> "FakeArgs":
    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


def test_cmd_symbols_kind_file_surfaces_file_nodes(tmp_path: Path, capsys) -> None:
    """WI-sufuh: `--kind file` surfaces file nodes despite the default exclusion."""
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(_excluded_kind_map()))

    result = cmd_symbols(_symbols_args(tmp_path, kind="file"))

    assert result == 0
    out, _ = capsys.readouterr()
    assert "utils_file" in out
    # Only file-kind requested → the function and variable are filtered out.
    assert "main_func" not in out
    assert "--brand-color" not in out


def test_cmd_symbols_kind_variable_surfaces_variable_nodes(
    tmp_path: Path, capsys
) -> None:
    """WI-sufuh: `--kind variable` surfaces variable nodes (else hidden)."""
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(_excluded_kind_map()))

    result = cmd_symbols(_symbols_args(tmp_path, kind="variable"))

    assert result == 0
    out, _ = capsys.readouterr()
    assert "--brand-color" in out
    assert "utils_file" not in out


def test_cmd_symbols_kind_function_still_excludes_low_value_kinds(
    tmp_path: Path, capsys
) -> None:
    """WI-sufuh regression fence: asking for a non-excluded kind keeps the
    silent exclusion active for the OTHER kinds (the bypass is scoped to the
    exact kind requested)."""
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(_excluded_kind_map()))

    result = cmd_symbols(_symbols_args(tmp_path, kind="function"))

    assert result == 0
    out, _ = capsys.readouterr()
    assert "main_func" in out
    assert "utils_file" not in out
    assert "--brand-color" not in out


def test_cmd_symbols_extreme_sink_dampened(tmp_path: Path, capsys) -> None:
    """Extreme pure sinks (in=100, out=0) rank below architectural connectors.

    Regression test for DEEP bakeoff cohort 15-16 finding: noopMetric.Inc
    (in=109, out=0, degree=109) and Timer.Duration (in=98, out=1, degree=99)
    dominate the top-5 symbols despite being trivial stubs, while genuine
    architectural hubs like evaluator.eval (in=10, out=78, degree=88) are
    buried.  The raw formula ``in * (1 + ln(1 + out))`` doesn't sufficiently
    penalize pure sinks when their in-degree is 10x higher.

    Sink dampening: symbols with out_degree/in_degree < 0.1 (near-pure sinks)
    get their effective in-degree reduced, so connectors with balanced in/out
    edges float to the top.

    Without dampening:
      noopStub:  100 * (1 + ln(1)) = 100.0
      connector:  10 * (1 + ln(71)) = 52.6
    → noopStub wins.

    With sink dampening (factor ~0.3 for ratio=0):
      noopStub:  100 * 0.3 * 1.0 = 30.0
      connector: 10 * 1.0 * 5.26 = 52.6
    → connector wins.
    """
    sink_node = {
        "id": "go:src/noop.go:1-5:noopStub.Inc:method",
        "name": "noopStub.Inc",
        "kind": "method",
        "language": "go",
        "path": "src/noop.go",
        "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 5},
    }
    connector_node = {
        "id": "go:src/engine.go:1-200:evaluator.eval:method",
        "name": "evaluator.eval",
        "kind": "method",
        "language": "go",
        "path": "src/engine.go",
        "span": {"start_line": 1, "end_line": 200, "start_col": 0, "end_col": 5},
    }

    # Create 100 callers for the sink and 70 callees + 10 callers for connector
    nodes = [sink_node, connector_node]
    edges = []
    eid = 1

    # 100 callers → noopStub.Inc (pure sink: in=100, out=0)
    for i in range(100):
        caller_id = f"go:src/caller{i}.go:1-5:caller{i}:function"
        nodes.append({
            "id": caller_id, "name": f"caller{i}", "kind": "function",
            "language": "go", "path": f"src/caller{i}.go",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 5},
        })
        edges.append({
            "id": f"edge:{eid}", "src": caller_id,
            "dst": sink_node["id"], "type": "calls", "confidence": 0.9,
        })
        eid += 1

    # 10 callers → evaluator.eval (connector: in=10)
    for i in range(10):
        caller_id = f"go:src/caller{i}.go:1-5:caller{i}:function"
        edges.append({
            "id": f"edge:{eid}", "src": caller_id,
            "dst": connector_node["id"], "type": "calls", "confidence": 0.9,
        })
        eid += 1

    # evaluator.eval → 70 callees (connector: out=70)
    for i in range(70):
        callee_id = f"go:src/callee{i}.go:1-5:callee{i}:function"
        nodes.append({
            "id": callee_id, "name": f"callee{i}", "kind": "function",
            "language": "go", "path": f"src/callee{i}.go",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 5},
        })
        edges.append({
            "id": f"edge:{eid}", "src": connector_node["id"],
            "dst": callee_id, "type": "calls", "confidence": 0.9,
        })
        eid += 1

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": edges,
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 5
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)
    assert result == 0

    out, _ = capsys.readouterr()
    eval_pos = out.find("evaluator.eval")
    noop_pos = out.find("noopStub.Inc")
    assert eval_pos != -1, f"evaluator.eval not found in output: {out}"
    assert noop_pos != -1, f"noopStub.Inc not found in output: {out}"
    assert eval_pos < noop_pos, (
        f"evaluator.eval (connector, in=10 out=70) should rank above "
        f"noopStub.Inc (pure sink, in=100 out=0) with sink dampening, "
        f"but found eval at {eval_pos}, noop at {noop_pos}"
    )


def test_cmd_symbols_utility_symbol_dampened(tmp_path: Path, capsys) -> None:
    """Utility symbols (exceptions, loggers) rank below domain symbols.

    Regression test for DEEP bakeoff cohort 8: GuacamoleServerException
    (in=81, out=2, deg=83) ranked position 8, pushing domain-relevant
    classes like ObjectPermission and UserService far below their
    architectural importance.

    Exception classes have non-zero out-degree (from extends/implements
    edges), so sink dampening alone doesn't sufficiently demote them.
    The fix: apply is_utility_symbol() dampening in cmd_symbols() to
    reduce the effective score of infrastructure symbols.

    Without utility dampening (sink dampening alone):
      SomeException:  in=80, out=2 → sink_factor ~0.35 → 28.2 * 2.099 = 59.2
      DomainService:  in=8,  out=15 → sink_factor 1.0  → 8.0 * 3.773 = 30.2
    → SomeException wins (wrong: infrastructure outranks domain logic).

    With utility dampening (0.10x applied to exception names):
      SomeException:  59.2 * 0.10 = 5.92
      DomainService:  30.2 * 1.0  = 30.2
    → DomainService wins (correct: domain logic surfaces above infrastructure).
    """
    exception_node = {
        "id": "java:src/SomeException.java:1-10:SomeException:class",
        "name": "SomeException",
        "kind": "class",
        "language": "java",
        "path": "src/SomeException.java",
        "span": {"start_line": 1, "end_line": 10, "start_col": 0, "end_col": 5},
    }
    domain_node = {
        "id": "java:src/DomainService.java:1-200:DomainService:class",
        "name": "DomainService",
        "kind": "class",
        "language": "java",
        "path": "src/DomainService.java",
        "span": {"start_line": 1, "end_line": 200, "start_col": 0, "end_col": 5},
    }

    nodes = [exception_node, domain_node]
    edges = []
    eid = 1

    # 80 callers → SomeException (high in-degree exception class)
    for i in range(80):
        caller_id = f"java:src/caller{i}.java:1-5:caller{i}:function"
        nodes.append({
            "id": caller_id, "name": f"caller{i}", "kind": "function",
            "language": "java", "path": f"src/caller{i}.java",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 5},
        })
        edges.append({
            "id": f"edge:{eid}", "src": caller_id,
            "dst": exception_node["id"], "type": "calls", "confidence": 0.9,
        })
        eid += 1

    # SomeException extends BaseException (out=2)
    base_exc = {
        "id": "java:src/BaseException.java:1-5:BaseException:class",
        "name": "BaseException",
        "kind": "class",
        "language": "java",
        "path": "src/BaseException.java",
        "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 5},
    }
    nodes.append(base_exc)
    edges.append({
        "id": f"edge:{eid}", "src": exception_node["id"],
        "dst": base_exc["id"], "type": "extends", "confidence": 1.0,
    })
    eid += 1
    edges.append({
        "id": f"edge:{eid}", "src": exception_node["id"],
        "dst": base_exc["id"], "type": "calls", "confidence": 0.9,
    })
    eid += 1

    # 8 callers → DomainService (moderate in-degree, domain class)
    for i in range(8):
        caller_id = f"java:src/caller{i}.java:1-5:caller{i}:function"
        edges.append({
            "id": f"edge:{eid}", "src": caller_id,
            "dst": domain_node["id"], "type": "calls", "confidence": 0.9,
        })
        eid += 1

    # DomainService → 15 callees (balanced connector)
    for i in range(15):
        callee_id = f"java:src/callee{i}.java:1-5:callee{i}:function"
        nodes.append({
            "id": callee_id, "name": f"callee{i}", "kind": "function",
            "language": "java", "path": f"src/callee{i}.java",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 5},
        })
        edges.append({
            "id": f"edge:{eid}", "src": domain_node["id"],
            "dst": callee_id, "type": "calls", "confidence": 0.9,
        })
        eid += 1

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": edges,
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 5
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)
    assert result == 0

    out, _ = capsys.readouterr()
    domain_pos = out.find("DomainService")
    exc_pos = out.find("SomeException")
    assert domain_pos != -1, f"DomainService not found in output: {out}"
    assert exc_pos != -1, f"SomeException not found in output: {out}"
    assert domain_pos < exc_pos, (
        f"DomainService (connector, in=8 out=15) should rank above "
        f"SomeException (utility exception class, in=80 out=2) with utility dampening, "
        f"but found domain at {domain_pos}, exception at {exc_pos}"
    )


def test_main_with_symbols(tmp_path: Path, capsys) -> None:
    """Main with symbols command."""
    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": "python:src/main.py:1-5:main:function",
                "name": "main",
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

    result = main(["symbols", "--path", str(tmp_path)])

    assert result == 0

    out, _ = capsys.readouterr()
    assert "main" in out


def test_cmd_symbols_low_confidence_edges_excluded_from_ranking(
    tmp_path: Path, capsys,
) -> None:
    """Low-confidence edges should not inflate ranking.

    Reproduces the memSeries.labels bug: a symbol with 50 low-confidence
    (0.49) in-edges was ranked #1 despite having negligible real
    connectivity.  The ranking pipeline must filter low-confidence edges
    just like the display pipeline does.

    Setup:
    - ``FalsePositive``: 50 low-confidence (0.49) in-edges, 2 out
      (non-trivial body so trivial-sink dampening doesn't mask the bug)
    - ``RealHub``: 10 high-confidence (0.9) in-edges, 5 out
    RealHub should outrank FalsePositive.
    """
    nodes = [
        {
            "id": "go:src/fp.go:1-100:FalsePositive:method",
            "name": "FalsePositive",
            "kind": "method",
            "language": "go",
            "path": "src/fp.go",
            "span": {"start_line": 1, "end_line": 100, "start_col": 0, "end_col": 10},
        },
        {
            "id": "go:src/hub.go:1-50:RealHub:function",
            "name": "RealHub",
            "kind": "function",
            "language": "go",
            "path": "src/hub.go",
            "span": {"start_line": 1, "end_line": 50, "start_col": 0, "end_col": 10},
        },
    ]
    edges = []
    edge_id = 1

    # 50 low-confidence callers -> FalsePositive (name collision artifacts)
    for i in range(50):
        caller_id = f"go:src/lc{i}.go:1-5:lc_caller{i}:function"
        nodes.append({
            "id": caller_id,
            "name": f"lc_caller{i}",
            "kind": "function",
            "language": "go",
            "path": f"src/lc{i}.go",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        })
        edges.append({
            "id": f"edge:{edge_id}",
            "src": caller_id,
            "dst": "go:src/fp.go:1-100:FalsePositive:method",
            "type": "calls",
            "line": 3,
            "confidence": 0.49,
        })
        edge_id += 1

    # Give FalsePositive 2 out-edges so it's not a pure sink
    for i in range(2):
        out_id = f"go:src/fpout{i}.go:1-5:fp_target{i}:function"
        nodes.append({
            "id": out_id,
            "name": f"fp_target{i}",
            "kind": "function",
            "language": "go",
            "path": f"src/fpout{i}.go",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        })
        edges.append({
            "id": f"edge:{edge_id}",
            "src": "go:src/fp.go:1-100:FalsePositive:method",
            "dst": out_id,
            "type": "calls",
            "line": 50,
            "confidence": 0.9,
        })
        edge_id += 1

    # 10 high-confidence callers -> RealHub + 5 outgoing from RealHub
    for i in range(10):
        caller_id = f"go:src/hc{i}.go:1-5:hc_caller{i}:function"
        nodes.append({
            "id": caller_id,
            "name": f"hc_caller{i}",
            "kind": "function",
            "language": "go",
            "path": f"src/hc{i}.go",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        })
        edges.append({
            "id": f"edge:{edge_id}",
            "src": caller_id,
            "dst": "go:src/hub.go:1-50:RealHub:function",
            "type": "calls",
            "line": 3,
            "confidence": 0.9,
        })
        edge_id += 1

    for i in range(5):
        target_id = f"go:src/tgt{i}.go:1-5:target{i}:function"
        nodes.append({
            "id": target_id,
            "name": f"target{i}",
            "kind": "function",
            "language": "go",
            "path": f"src/tgt{i}.go",
            "span": {"start_line": 1, "end_line": 5, "start_col": 0, "end_col": 10},
        })
        edges.append({
            "id": f"edge:{edge_id}",
            "src": "go:src/hub.go:1-50:RealHub:function",
            "dst": target_id,
            "type": "calls",
            "line": 10,
            "confidence": 0.9,
        })
        edge_id += 1

    behavior_map = {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": edges,
    }
    results_file = tmp_path / "hypergumbo.results.json"
    results_file.write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)
    assert result == 0

    out, _ = capsys.readouterr()
    hub_pos = out.find("RealHub")
    fp_pos = out.find("FalsePositive")
    assert hub_pos != -1, "RealHub should appear in output"
    assert fp_pos != -1, "FalsePositive should appear in output"
    assert hub_pos < fp_pos, (
        "RealHub (10 high-confidence in-edges) should rank above "
        "FalsePositive (50 low-confidence in-edges). "
        "Ranking must filter low-confidence edges (conf < 0.5)."
    )


# ---------------------------------------------------------------------------
# Column-width controls (Symbol / File)
# ---------------------------------------------------------------------------

def _stub_terminal_size(monkeypatch, width: int) -> None:
    """Pin the detected terminal width so column-width assertions are stable.

    ``cmd_symbols`` reads ``shutil.get_terminal_size()`` to size the Rich
    console. Under capsys the underlying handle is not a TTY, so the
    detected width depends on the runner's environment. Tests stub the
    function to a fixed value so they don't drift between hosts.
    """
    import os

    fake = os.terminal_size((width, 24))
    monkeypatch.setattr(
        "hypergumbo_core.cli.shutil.get_terminal_size",
        lambda *a, **kw: fake,
    )


def _build_long_path_behavior_map(long_path: str, long_name: str = "f") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {
                "id": f"python:{long_path}:1-5:{long_name}:function",
                "name": long_name,
                "kind": "function",
                "language": "python",
                "path": long_path,
                "span": {
                    "start_line": 1, "end_line": 5,
                    "start_col": 0, "end_col": 10,
                },
            },
        ],
        "edges": [],
    }


def test_symbols_column_config_defaults() -> None:
    """Default config: Symbol min_width=60, File min_width=80, ellipsis truncation."""
    symbol_w, file_w, overflow, no_wrap = _symbols_column_config(
        col_width=None, wrap=False,
    )
    assert symbol_w == 60
    assert file_w == 80
    assert overflow == "ellipsis"
    assert no_wrap is True


def test_symbols_column_config_wrap_flag() -> None:
    """--wrap selects fold-overflow with no_wrap=False."""
    _, _, overflow, no_wrap = _symbols_column_config(col_width=None, wrap=True)
    assert overflow == "fold"
    assert no_wrap is False


def test_symbols_column_config_col_width_applies_to_both_columns() -> None:
    """--col-width N sets both Symbol and File to N (within bounds)."""
    symbol_w, file_w, _, _ = _symbols_column_config(col_width=200, wrap=False)
    assert symbol_w == 200
    assert file_w == 200


def test_symbols_column_config_col_width_capped_at_1000() -> None:
    """Values above 1000 are clamped to 1000 (sanity bound)."""
    symbol_w, file_w, _, _ = _symbols_column_config(col_width=99999, wrap=False)
    assert symbol_w == 1000
    assert file_w == 1000


def test_symbols_column_config_col_width_floor_at_one() -> None:
    """Zero / negative column widths are clamped to 1 (Rich requires positive)."""
    symbol_w, file_w, _, _ = _symbols_column_config(col_width=0, wrap=False)
    assert symbol_w == 1
    assert file_w == 1
    symbol_w, file_w, _, _ = _symbols_column_config(col_width=-50, wrap=False)
    assert symbol_w == 1
    assert file_w == 1


def test_symbols_column_config_col_width_with_wrap() -> None:
    """--col-width and --wrap can combine."""
    symbol_w, file_w, overflow, no_wrap = _symbols_column_config(
        col_width=300, wrap=True,
    )
    assert symbol_w == 300
    assert file_w == 300
    assert overflow == "fold"
    assert no_wrap is False


def test_cmd_symbols_default_truncates_long_path(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    """Default rendering truncates a path that exceeds the column with ``…``."""
    _stub_terminal_size(monkeypatch, 80)
    long_path = "src/" + "/".join(f"deep_segment_{i}" for i in range(20)) + "/main.py"
    behavior_map = _build_long_path_behavior_map(long_path)
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None

    result = cmd_symbols(args)
    assert result == 0

    out, _ = capsys.readouterr()
    assert "…" in out, "Expected ellipsis truncation marker in default output"
    assert long_path not in out, (
        "Full long path should not appear by default (truncated)."
    )


def test_cmd_symbols_wrap_flag_renders_full_long_path(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    """--wrap folds the path across lines so every character is rendered."""
    _stub_terminal_size(monkeypatch, 80)
    long_path = "src/" + "/".join(f"seg_{i}" for i in range(15)) + "/file.py"
    behavior_map = _build_long_path_behavior_map(long_path)
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None
    args.wrap = True

    result = cmd_symbols(args)
    assert result == 0

    out, _ = capsys.readouterr()
    out = _strip_ansi(out)
    assert "…" not in out, "Wrap mode should not produce ellipsis truncation"
    # Fold-wrap can split a segment at the column boundary
    # (`seg_11/se` ends one line, `g_12/...` begins the next), so the path
    # is not a contiguous substring of `out`. After stripping whitespace
    # the wrapped fragments rejoin and the full path reappears.
    flattened = "".join(out.split())
    assert long_path in flattened, (
        f"Wrapped path not fully recoverable from output. "
        f"Expected {long_path!r} after whitespace strip."
    )


def test_cmd_symbols_col_width_widens_both_columns(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    """--col-width N renders longer content fully without ellipsis."""
    _stub_terminal_size(monkeypatch, 80)
    # A path ~140 chars: would be truncated under default 80-wide File column,
    # but fits cleanly when --col-width pushes it to 200.
    long_path = "src/" + "/".join(f"segment_{i:02d}" for i in range(12)) + "/main.py"
    assert len(long_path) > 80
    behavior_map = _build_long_path_behavior_map(long_path)
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None
    args.col_width = 200

    result = cmd_symbols(args)
    assert result == 0

    out, _ = capsys.readouterr()
    assert long_path in out, (
        "Full path should be visible at --col-width 200; got truncated output."
    )
    assert "…" not in out


def test_cmd_symbols_col_width_clamped_at_1000(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    """--col-width above 1000 is silently clamped (no crash, content visible)."""
    _stub_terminal_size(monkeypatch, 80)
    behavior_map = _build_long_path_behavior_map("src/short.py")
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

    args = FakeArgs()
    args.path = str(tmp_path)
    args.input = None
    args.kind = None
    args.language = None
    args.limit = 200
    args.all = False
    args.exclude_tests = False
    args.max_per_file = None
    args.col_width = 99999

    result = cmd_symbols(args)
    assert result == 0
    out, _ = capsys.readouterr()
    assert "src/short.py" in out


def test_main_symbols_col_width_and_wrap_args(
    tmp_path: Path, capsys, monkeypatch,
) -> None:
    """``--col-width`` and ``--wrap`` parse cleanly through the top-level CLI."""
    _stub_terminal_size(monkeypatch, 80)
    behavior_map = _build_long_path_behavior_map("src/a.py", long_name="my_func")
    (tmp_path / "hypergumbo.results.json").write_text(json.dumps(behavior_map))

    result = main([
        "symbols", "--path", str(tmp_path),
        "--col-width", "150", "--wrap",
    ])
    assert result == 0
    out, _ = capsys.readouterr()
    assert "my_func" in out
