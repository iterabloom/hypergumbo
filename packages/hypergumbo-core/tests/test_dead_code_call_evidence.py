# SPDX-License-Identifier: AGPL-3.0-or-later
# covers: packages/hypergumbo-core/src/hypergumbo_core/cli.py
"""``dead-code-maybe`` reports a callable only where the run can see callers.

The view lists every production function or method no seed reaches. "No edge
reaches it" is evidence of no caller only if the analyzer of that language
emits call edges onto that kind of symbol at all. Where it emits none, every
callable of the language is unreached, and the view listed all of them: a
Verilog fixture went from "no production functions" to 13 of 13 dead the day
its analyzer started emitting function symbols.

The rule: a ``(language, kind)`` group has CALL EVIDENCE when the run holds at
least one call-family edge (``calls`` / ``instantiates``) that lands on an
in-repo callable of that language and kind. Unreached callables of a group
without it are not reported as dead candidates; they are listed apart, with
their group, under ``no_call_evidence_withheld`` / ``no_call_evidence_groups``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import cmd_dead_code_maybe
from hypergumbo_core.schema import DEAD_CODE_MAYBE_SCHEMA_VERSION


def _fn(
    language: str, path: str, line: int, name: str, kind: str = "function",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": f"{language}:{path}:{line}-{line + 3}:{name}:{kind}",
        "name": name, "kind": kind, "language": language, "path": path,
        "span": {"start_line": line, "end_line": line + 3}, "line_span": 4,
        **extra,
    }


def _file(language: str, path: str) -> dict[str, Any]:
    """A file node: the source of a call made at file level. Not a callable,
    so it is neither a candidate nor (through the connectivity fallback) a
    seed, and an edge out of it changes nothing but the evidence."""
    return {
        "id": f"{language}:{path}:1-1:file:file", "name": path, "kind": "file",
        "language": language, "path": path,
        "span": {"start_line": 1, "end_line": 1},
    }


def _edge(src: dict[str, Any], dst: dict[str, Any], edge_type: str = "calls") -> dict:
    return {"type": edge_type, "src": src["id"], "dst": dst["id"]}


def _run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], nodes: list, edges: list,
    fmt: str = "json", **flags: Any,
) -> Any:
    bm = tmp_path / "hg.json"
    bm.write_text(json.dumps(
        {"schema_version": "0.2.3", "nodes": nodes, "edges": edges}
    ))
    args = argparse.Namespace(
        path=str(tmp_path), input=str(bm), format=fmt,
        seeds=flags.pop("seeds", "entrypoints"), min_confidence=0.0, **flags,
    )
    assert cmd_dead_code_maybe(args) == 0
    out = capsys.readouterr().out
    return json.loads(out) if fmt == "json" else out


def _names(records: list[dict]) -> list[str]:
    return sorted(r["name"] for r in records)


VERILOG = [
    _fn("verilog", "alu.v", 1, "add"),
    _fn("verilog", "alu.v", 10, "sub"),
    _fn("verilog", "alu.v", 20, "mux"),
]
TOP = _file("verilog", "top.v")


class TestNoCallEvidence:
    def test_a_language_with_no_call_edge_is_withheld_not_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        out = _run(tmp_path, capsys, VERILOG, [])

        assert out["dead_candidates"] == []
        assert out["summary"]["dead_candidates"] == 0
        assert out["summary"]["dead_percent"] == 0.0
        assert out["summary"]["withheld_no_call_evidence"] == 3
        assert _names(out["no_call_evidence_withheld"]) == ["add", "mux", "sub"]
        assert out["no_call_evidence_groups"] == [
            {"language": "verilog", "kind": "function", "callables": 3, "withheld": 3},
        ]
        withheld = out["no_call_evidence_withheld"][0]
        assert set(withheld) == {
            "name", "path", "language", "kind", "line_span", "span", "id",
        }

    def test_one_call_edge_onto_the_group_is_evidence_for_all_of_it(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        out = _run(tmp_path, capsys, [*VERILOG, TOP], [_edge(TOP, VERILOG[1])])

        # No seed reaches any of them; all three are now reported.
        assert _names(out["dead_candidates"]) == ["add", "mux", "sub"]
        assert out["summary"]["withheld_no_call_evidence"] == 0
        assert out["no_call_evidence_withheld"] == []
        assert out["no_call_evidence_groups"] == []

    def test_evidence_is_per_kind(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Functions are called, no method ever is: the methods are withheld
        and the unreached function is still reported."""
        script = _file("fsharp", "main.fsx")
        callee = _fn("fsharp", "m.fs", 10, "helper")
        orphan = _fn("fsharp", "m.fs", 20, "orphan")
        member = _fn("fsharp", "t.fs", 1, "T.Render", "method")
        other = _fn("fsharp", "t.fs", 10, "T.Size", "method")

        out = _run(
            tmp_path, capsys, [script, callee, orphan, member, other],
            [_edge(script, callee)],
        )

        assert _names(out["dead_candidates"]) == ["helper", "orphan"]
        assert _names(out["no_call_evidence_withheld"]) == ["T.Render", "T.Size"]
        assert out["no_call_evidence_groups"] == [
            {"language": "fsharp", "kind": "method", "callables": 2, "withheld": 2},
        ]

    def test_evidence_is_per_language(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        py_script = _file("python", "run.py")
        py_callee = _fn("python", "a.py", 10, "helper")

        out = _run(
            tmp_path, capsys, [py_script, py_callee, *VERILOG],
            [_edge(py_script, py_callee)],
        )

        assert _names(out["dead_candidates"]) == ["helper"]
        assert _names(out["no_call_evidence_withheld"]) == ["add", "mux", "sub"]

    @pytest.mark.parametrize("edge_type", ["dispatches_to", "wraps", "contains", "imports"])
    def test_only_a_call_edge_is_call_evidence(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], edge_type: str,
    ) -> None:
        """A route or a decorator reaching a function says a linker ran, not
        that the analyzer sees callers."""
        out = _run(
            tmp_path, capsys, [*VERILOG, TOP], [_edge(TOP, VERILOG[1], edge_type)],
        )

        assert out["dead_candidates"] == []
        assert out["summary"]["withheld_no_call_evidence"] == 3

    def test_an_instantiates_edge_is_call_evidence(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        out = _run(
            tmp_path, capsys, [*VERILOG, TOP],
            [_edge(TOP, VERILOG[1], "instantiates")],
        )

        assert out["summary"]["withheld_no_call_evidence"] == 0

    def test_a_call_from_test_code_onto_test_code_is_evidence(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The question is whether the analyzer emits such edges, not whether
        production code is called."""
        test_file = _file("verilog", "tests/test_alu.v")
        test_fn = _fn("verilog", "tests/test_alu.v", 10, "check")

        out = _run(
            tmp_path, capsys, [*VERILOG, test_file, test_fn],
            [_edge(test_file, test_fn)],
        )

        assert _names(out["dead_candidates"]) == ["add", "mux", "sub"]

    def test_a_call_onto_a_stand_in_is_not_evidence(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A linker's stand-in is not a declaration: an edge landing on it
        does not show the analyzer resolves callers of in-repo code."""
        stand_in = _fn(
            "verilog", "alu.v", 40, "remote_add", protocol_origin="grpc",
        )

        out = _run(
            tmp_path, capsys, [*VERILOG, TOP, stand_in], [_edge(TOP, stand_in)],
        )

        assert out["dead_candidates"] == []
        assert out["summary"]["withheld_no_call_evidence"] == 3

    def test_a_call_onto_another_kind_is_not_evidence(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        module = _fn("verilog", "top.v", 1, "top", "module")

        out = _run(
            tmp_path, capsys, [*VERILOG, TOP, module], [_edge(TOP, module)],
        )

        assert out["dead_candidates"] == []
        assert out["summary"]["withheld_no_call_evidence"] == 3

    def test_only_unreached_callables_are_withheld(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A seed of a group without evidence is reached: it is counted in the
        group's callables and not listed."""
        exported = _fn(
            "verilog", "alu.v", 40, "top_fn", supply_chain={"is_exported": True},
        )

        out = _run(tmp_path, capsys, [*VERILOG, exported], [], seeds="production")

        assert _names(out["no_call_evidence_withheld"]) == ["add", "mux", "sub"]
        assert out["no_call_evidence_groups"] == [
            {"language": "verilog", "kind": "function", "callables": 4, "withheld": 3},
        ]

    def test_what_the_view_never_lists_is_not_listed_as_withheld(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Generated code is dropped, and ``--exclude-exports`` drops exports,
        before the evidence question is asked."""
        generated = _fn(
            "verilog", "gen.v", 1, "gen_fn",
            supply_chain={"is_generated_file": True},
        )
        exported = _fn(
            "verilog", "alu.v", 40, "top_fn", supply_chain={"is_exported": True},
        )

        out = _run(
            tmp_path, capsys, [*VERILOG, generated, exported], [],
            exclude_exports=True,
        )

        assert _names(out["no_call_evidence_withheld"]) == ["add", "mux", "sub"]
        assert out["no_call_evidence_groups"] == [
            {"language": "verilog", "kind": "function", "callables": 5, "withheld": 3},
        ]

    def test_groups_are_listed_largest_first_then_by_name(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        nodes = [
            _fn("zig", "a.zig", 1, "one"),
            *VERILOG,
            _fn("ada", "a.adb", 1, "one"),
        ]

        out = _run(tmp_path, capsys, nodes, [])

        assert [(g["language"], g["withheld"]) for g in out["no_call_evidence_groups"]] == [
            ("verilog", 3), ("ada", 1), ("zig", 1),
        ]

    def test_schema_version_is_the_one_that_added_the_fields(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        out = _run(tmp_path, capsys, VERILOG, [])

        assert out["schema_version"] == DEAD_CODE_MAYBE_SCHEMA_VERSION
        assert tuple(int(p) for p in DEAD_CODE_MAYBE_SCHEMA_VERSION.split(".")) >= (0, 4, 0)


class TestNoCallEvidenceText:
    def test_text_names_the_groups_and_does_not_list_them_as_dead(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        text = _run(tmp_path, capsys, VERILOG, [], fmt="text")

        assert "withheld, no call evidence:      3" in text
        assert "verilog function: 3 of 3 unreached, not reported" in text
        assert "No potentially dead functions found." in text
        assert "add " not in text

    def test_text_says_zero_when_nothing_is_withheld(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        text = _run(
            tmp_path, capsys, [*VERILOG, TOP], [_edge(TOP, VERILOG[1])], fmt="text",
        )

        assert "withheld, no call evidence:      0" in text
        assert "unreached, not reported" not in text
