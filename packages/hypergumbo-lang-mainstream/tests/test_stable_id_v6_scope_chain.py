# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests for the stable_id v6 scope-chain contract (ADR-0035 §1, WI-gitun).

v6 makes ``stable_id`` unique-within-run by folding the FULL enclosing scope chain
(enclosing classes AND enclosing functions) into the hash. Two semantically distinct
symbols — same local name, distinct scope — must therefore get distinct stable_ids.

These are the cases v5 collapsed (the WI-gitun / INV-tazaj defect):

* **Function-local classes** — N identical-body ``class Args`` each nested in a distinct
  enclosing function collapsed to ONE stable_id under v5 (``containing`` was the file and
  ``name`` was the bare local name). The canonical repro is 8 ``class Args`` in distinct
  test functions sharing ``sha256:3cd0dfc7ff321942``.
* **The enclosing-class case** — a class defined inside a *method* of two different classes
  (``class A: def t(): class Mock`` vs ``class B: def t(): class Mock``). A function-only
  scope chain still collapses these (it sees only ``t``); the full chain (``A.t`` vs ``B.t``)
  splits them.
* **The analogous nested-function case** — a function nested in a method of two different
  classes must likewise split.

v6 also DROPS ``body_sig`` from the class hash (it churned the class id on every member
add/remove, violating §1 "survives body edits"; structural identity is ``shape_id``'s job).
With the full scope chain, dropping ``body_sig`` introduces no same-scope collision on the
self-corpus (measured: 0 of 4345 classes were distinguished by ``body_sig`` alone).
"""
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _run_and_load(tmp_path: Path, src: str, filename: str = "mod.py") -> dict:
    (tmp_path / filename).write_text(src)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _stable_ids_for(data: dict, name_suffix: str, kind: str) -> list[str]:
    """stable_ids of nodes of ``kind`` whose name ends with ``name_suffix``."""
    return [
        n["stable_id"]
        for n in data["nodes"]
        if n.get("kind") == kind and n.get("name", "").split(".")[-1] == name_suffix
    ]


def test_function_local_classes_distinct(tmp_path: Path) -> None:
    """N identical ``class Args`` in distinct enclosing functions get N distinct ids."""
    src = "\n".join(
        f"def case_{i}():\n    class Args:\n        pass\n    return Args\n" for i in range(8)
    )
    data = _run_and_load(tmp_path, src)
    ids = _stable_ids_for(data, "Args", "class")
    assert len(ids) == 8, f"expected 8 Args class nodes, got {len(ids)}"
    assert len(set(ids)) == 8, f"WI-gitun: 8 function-local Args collapsed to {len(set(ids))} ids"


def test_local_class_in_methods_of_distinct_classes_distinct(tmp_path: Path) -> None:
    """class A: def t(): class Mock  vs  class B: def t(): class Mock — must split.

    Function-only scope chains collapse these (both see enclosing function ``t``); the full
    enclosing-class chain (A.t vs B.t) is required.
    """
    src = (
        "class A:\n"
        "    def t(self):\n"
        "        class Mock:\n"
        "            x = 1\n"
        "        return Mock\n"
        "class B:\n"
        "    def t(self):\n"
        "        class Mock:\n"
        "            x = 1\n"
        "        return Mock\n"
    )
    data = _run_and_load(tmp_path, src)
    ids = _stable_ids_for(data, "Mock", "class")
    assert len(ids) == 2, f"expected 2 Mock class nodes, got {len(ids)}"
    assert len(set(ids)) == 2, "enclosing-class scope: two Mock classes collapsed to one id"


def test_nested_function_in_methods_of_distinct_classes_distinct(tmp_path: Path) -> None:
    """class A: def t(): def helper()  vs  class B: def t(): def helper() — must split."""
    src = (
        "class A:\n"
        "    def t(self):\n"
        "        def helper():\n"
        "            return 1\n"
        "        return helper\n"
        "class B:\n"
        "    def t(self):\n"
        "        def helper():\n"
        "            return 1\n"
        "        return helper\n"
    )
    data = _run_and_load(tmp_path, src)
    ids = _stable_ids_for(data, "helper", "function")
    assert len(ids) == 2, f"expected 2 helper function nodes, got {len(ids)}"
    assert len(set(ids)) == 2, "enclosing-class scope: two nested helper functions collapsed"
