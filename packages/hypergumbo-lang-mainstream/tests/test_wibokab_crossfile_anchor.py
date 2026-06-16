# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-bokab (v7) behavioral guard across the producer wiring classes.

The completeness lint (hypergumbo-core/tests/test_wibokab_producer_anchoring_lint.py)
proves a ``file_stable_id=`` argument is *present* at every producer call, but it is
value-blind. These integration tests prove, through real analysis, that the folded value
is *correct*:

* **cross-file uniqueness** — two same-named top-level symbols in DIFFERENT files get
  DISTINCT stable_ids (the collision WI-bokab closes); and
* **location independence** — the same file under DIFFERENT absolute roots gets IDENTICAL
  stable_ids (the anchor is the repo-relative path, never the absolute one — the
  regression the design's abs-path trap warns about).

One analyzer per wiring class so a wrong-value regression in any class is caught:
bash (base-loop untyped), ruby (custom-loop untyped — a fan-out blocker), go (custom-loop
typed — the absolute-path-trap class). rust (base-loop typed) is covered by
``test_wibokab_rust_file_anchor.py``.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.bash import analyze_bash
from hypergumbo_lang_mainstream.go import analyze_go
from hypergumbo_lang_mainstream.ruby import analyze_ruby


def _ids_named(result, name: str) -> list[str]:
    return [s.stable_id for s in result.symbols if s.name == name and s.stable_id]


# ---- bash: base-loop untyped (the original WI-bokab motivator: usage() across scripts) ----

def test_bash_same_name_function_distinct_across_files(tmp_path: Path) -> None:
    (tmp_path / "a.sh").write_text("usage() { echo a; }\n")
    (tmp_path / "b.sh").write_text("usage() { echo b; }\n")
    ids = _ids_named(analyze_bash(tmp_path), "usage")
    assert len(ids) == 2, f"expected one usage per file, got {len(ids)}"
    assert ids[0] != ids[1], "cross-file collision: bash usage() shares a stable_id"


def test_bash_stable_id_location_independent(tmp_path: Path) -> None:
    r1, r2 = tmp_path / "x" / "p", tmp_path / "y" / "p"
    for r in (r1, r2):
        r.mkdir(parents=True)
        (r / "m.sh").write_text("usage() { echo hi; }\n")
    a, b = _ids_named(analyze_bash(r1), "usage"), _ids_named(analyze_bash(r2), "usage")
    assert a and a == b, f"location-dependent bash stable_id: {a} != {b}"


# ---- ruby: custom-loop untyped (module-free _extract via the _analyzer singleton) ----

def test_ruby_same_name_method_distinct_across_files(tmp_path: Path) -> None:
    (tmp_path / "a.rb").write_text("def foo\n  1\nend\n")
    (tmp_path / "b.rb").write_text("def foo\n  2\nend\n")
    ids = _ids_named(analyze_ruby(tmp_path), "foo")
    assert len(ids) == 2, f"expected one foo per file, got {len(ids)}"
    assert ids[0] != ids[1], "cross-file collision: ruby def foo shares a stable_id"


def test_ruby_stable_id_location_independent(tmp_path: Path) -> None:
    r1, r2 = tmp_path / "x" / "p", tmp_path / "y" / "p"
    for r in (r1, r2):
        r.mkdir(parents=True)
        (r / "m.rb").write_text("def foo\n  1\nend\n")
    a, b = _ids_named(analyze_ruby(r1), "foo"), _ids_named(analyze_ruby(r2), "foo")
    assert a and a == b, f"location-dependent ruby stable_id: {a} != {b}"


# ---- go: custom-loop typed (the absolute-path-trap class; mirrors the docker-cred main() case) ----

def test_go_same_name_func_distinct_across_files(tmp_path: Path) -> None:
    (tmp_path / "cmd1").mkdir()
    (tmp_path / "cmd1" / "main.go").write_text("package main\nfunc Run() {}\n")
    (tmp_path / "cmd2").mkdir()
    (tmp_path / "cmd2" / "main.go").write_text("package main\nfunc Run() {}\n")
    ids = _ids_named(analyze_go(tmp_path), "Run")
    assert len(ids) == 2, f"expected one Run per file, got {len(ids)}"
    assert ids[0] != ids[1], "cross-file collision: go func Run shares a stable_id"


def test_go_stable_id_location_independent(tmp_path: Path) -> None:
    r1, r2 = tmp_path / "x" / "p", tmp_path / "y" / "p"
    for r in (r1, r2):
        (r / "cmd").mkdir(parents=True)
        (r / "cmd" / "main.go").write_text("package main\nfunc Run() {}\n")
    a, b = _ids_named(analyze_go(r1), "Run"), _ids_named(analyze_go(r2), "Run")
    assert a and a == b, f"location-dependent go stable_id: {a} != {b}"
