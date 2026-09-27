# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-bulaz on the production path: a partially parsed file is disclosed.

One broken and one clean file per language, surveyed end to end. The broken
file must appear in ``limits.failed_files`` under its repo-relative path with
the ``partial_parse`` reason; the clean one must not. Rust and C++ go through
the shared TreeSitterAnalyzer loop; the others override ``analyze()`` with
their own loops, which each had to be wired separately.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.analyze.base import PARTIAL_PARSE_REASON
from hypergumbo_core.cli import main

_CASES = {
    "rust": ("broken.rs", "fn f( {\n", "clean.rs", "fn g() {}\n"),
    "go": ("broken.go", "package main\nfunc f( {\n", "clean.go", "package main\nfunc g() {}\n"),
    "java": ("Broken.java", "class B { void f( { }\n", "Clean.java", "class C { void g() {} }\n"),
    "javascript": ("broken.js", "function f( {\n", "clean.js", "function g() {}\n"),
    "kotlin": ("broken.kt", "fun f( {\n", "clean.kt", "fun g() {}\n"),
    "c": ("broken.c", "int f( {\n", "clean.c", "int g(void) { return 0; }\n"),
    "cpp": ("broken.cpp", "int f( {\n", "clean.cpp", "int g() { return 0; }\n"),
    "objc": ("broken.m", "@implementation B\n- (void)f { [x \n@end\n",
             "clean.m", "@implementation C\n- (void)g {}\n@end\n"),
    "csharp": ("Broken.cs", "class B { void F( { }\n", "Clean.cs", "class C { void G() {} }\n"),
    "php": ("broken.php", "<?php\nfunction f( {\n", "clean.php", "<?php\nfunction g() {}\n"),
    "ruby": ("broken.rb", "def f(\n  x = [\nend\n", "clean.rb", "def g\nend\n"),
    "lua": ("broken.lua", "function f(\n", "clean.lua", "function g() end\n"),
    "swift": ("broken.swift", "func f( {\n", "clean.swift", "func g() {}\n"),
    "scala": ("broken.scala", "object B { def f( = 1\n", "clean.scala", "object C { def g() = 1 }\n"),
}


@pytest.mark.parametrize("lang", sorted(_CASES))
def test_a_broken_file_is_disclosed_and_a_clean_one_is_not(
    tmp_path: Path, lang: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken, broken_src, clean, clean_src = _CASES[lang]
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / broken).write_text(broken_src)
    (repo / clean).write_text(clean_src)
    if lang == "go":
        (repo / "go.mod").write_text("module example.com/t\n\ngo 1.21\n")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    main(["survey", str(repo), "--out", str(tmp_path / "s.json")])
    failed = json.loads((tmp_path / "s.json").read_text()).get("limits", {}).get("failed_files", [])
    partial = {f["path"] for f in failed if f["reason"].startswith(PARTIAL_PARSE_REASON)}
    assert broken in partial, failed
    assert clean not in partial, failed
