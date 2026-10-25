# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-lisan: a pass that read files and emitted nothing says why.

``unreported`` is the orchestrator's word for "this pass opened files,
produced nothing, and did not say why". It was 0 on the self-survey and not
elsewhere: 4 on compose-go, 6 on component-model-demo, all different linkers,
because only eleven linker bodies ever claimed a reason. A zero in a drained
population measures the corpus, not completion; so the first test here builds
a corpus whose every file is in a language some pass reads and holds no
construct any pass models, and runs every pass on it.

The claim is anchored on what each scan FOUND, never on what the pass emitted
(``silence_reason_for_candidates``). The targeted tests below exercise the
other branch: a construct the scan finds but cannot resolve is
``candidates_unresolved``, not ``no_candidate_construct``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.pass_silence import CANDIDATES_UNRESOLVED, NO_CANDIDATE_FILES, UNREPORTED

#: One construct-free file per language a pass reads.
_CONSTRUCT_FREE = {
    "a.py": "x = 1\n", "a.js": "const x = 1;\n", "a.ts": "const x: number = 1;\n",
    "a.go": "package main\n\nvar X = 1\n", "A.java": "class A {}\n", "a.rb": "x = 1\n",
    "a.rs": "fn f() {}\n", "a.c": "int x;\n", "a.php": "<?php $x = 1;\n",
    "a.kt": "val x = 1\n", "a.swift": "let x = 1\n", "a.cs": "class A {}\n",
    "a.sh": "echo hi\n", "a.yaml": "x: 1\n", "a.json": '{"a": 1}\n', "README.md": "# T\n",
    "a.ex": "defmodule A do\nend\n", "a.lua": "local x = 1\n",
    "a.vue": "<template><div/></template>\n", "a.proto": 'syntax = "proto3";\n',
    "a.graphql": "type Query { a: Int }\n", "a.sql": "SELECT 1;\n", "a.tex": "hello\n",
    # A C function gives the FFI bridges (napi, ruby-ffi) a target file to
    # scan; it holds no export or rb_define for them to find.
    "b.c": "int f(void) { return 0; }\n",
}


def _runs(root: Path, files: dict[str, str], out: Path, **kwargs: Any) -> dict[str, dict]:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False,
                     progress=False, **kwargs)
    return {r["pass"]: r for r in json.loads(out.read_text())["analysis_runs"]}


def test_no_pass_reads_files_and_stays_silent_without_a_reason(tmp_path: Path) -> None:
    """Measured before the fix with --frameworks all: 16 unreported passes --
    11 linkers (argparse-dispatch, database-query, event-sourcing, go-cobra,
    go-memberlist, graphql, http, lua-ffi, phoenix-ipc, subprocess, websocket)
    and 5 analyzers (c, json, proto, sql, vue). napi, ruby-ffi and latex
    surfaced on cowboy and confluent-examples once those were fixed."""
    repo = tmp_path / "repo"
    runs = _runs(repo, _CONSTRUCT_FREE, tmp_path / "bm.json", frameworks="all")
    read_files = [p for p, r in runs.items() if r.get("files_analyzed")]
    assert len(read_files) >= 20, read_files
    assert sorted(p for p, r in runs.items() if r.get("silence_reason") == UNREPORTED) == []


def test_a_scan_that_read_no_file_keeps_no_candidate_files(tmp_path: Path) -> None:
    """The first cut claimed no_candidate_construct for a content scan that
    read nothing: on compose-go, database-query, subprocess and websocket read
    0 files and lost the orchestrator's more precise no_candidate_files."""
    runs = _runs(tmp_path / "repo", {"main.go": "package main\n\nfunc main() {}\n"},
                 tmp_path / "bm.json")
    for pass_id in ("websocket-linker", "database-query-linker", "subprocess-linker",
                    "http-linker", "graphql-linker", "event-sourcing-linker"):
        run = runs.get(pass_id)
        if run is None or run.get("files_analyzed"):
            continue
        assert run.get("silence_reason") == NO_CANDIDATE_FILES, (pass_id, run)
    assert runs["websocket-linker"].get("files_analyzed") in (0, None)


def _silence(tmp_path: Path, files: dict[str, str], pass_id: str) -> str:
    runs = _runs(tmp_path / "repo", files, tmp_path / "bm.json")
    run = runs[pass_id]
    assert not run.get("edges_emitted"), run
    return str(run.get("silence_reason"))


class TestAFoundConstructThatDoesNotResolve:
    def test_argparse_handler_with_no_symbol(self, tmp_path: Path) -> None:
        source = ("import argparse\np = argparse.ArgumentParser()\n"
                  "p.set_defaults(func=nowhere)\n")
        assert _silence(tmp_path, {"cli.py": source}, "argparse-dispatch-linker") \
            == CANDIDATES_UNRESOLVED

    def test_cobra_handler_with_no_symbol(self, tmp_path: Path) -> None:
        source = ('package main\n\nimport "github.com/spf13/cobra"\n\n'
                  "var root = &cobra.Command{Use: \"x\", Run: nowhere}\n")
        assert _silence(tmp_path, {"main.go": source}, "go-cobra-linker") \
            == CANDIDATES_UNRESOLVED

    def test_memberlist_import_with_no_delegate(self, tmp_path: Path) -> None:
        source = ('package main\n\nimport "github.com/hashicorp/memberlist"\n\n'
                  "var c = memberlist.DefaultLANConfig()\n")
        assert _silence(tmp_path, {"main.go": source}, "go-memberlist-linker") \
            == CANDIDATES_UNRESOLVED
