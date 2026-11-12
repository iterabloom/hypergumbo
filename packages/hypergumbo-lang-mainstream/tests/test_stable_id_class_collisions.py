# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests: stable_id distinguishes symbols within and across compilation units.

The contract (stable_id v6, ADR-0035; ADR-0014):

* **Survives BODY edits** — same name, same shape, different body content
  produces the same stable_id. The class hash carries no member set
  (``body_sig``): that would churn the class id on every member add/remove;
  structural identity is ``shape_id``'s job (``test_method_order_irrelevant``).
* **Does NOT survive rename or move** — ``name`` and ``qualified_name`` (the
  full enclosing scope chain) are hash inputs, so rename and move are
  identity-changing operations. With shape-only inputs, every same-shape
  symbol in a file would share one id — e.g. ``@dataclass`` classes such as
  ir.py's ``Symbol``/``Edge``/``Span``, or zero-parameter test functions — and
  their same-named methods (``to_dict``) would collide in turn.

So two classes with identical body shapes in the same module split by *name*,
and the cases below are built that way.

Module identity is part of symbol identity under Python's import semantics, so
structurally identical classes and top-level functions in *different* modules
must also get distinct stable_ids. Top-level classes and functions take
``make_file_stable_id("python", repo_relative_path)`` as their containing
identity; without it the containing id is empty and module identity
drops out of the hash.

``_compute_stable_id`` treats ``ast.AsyncFunctionDef`` as a function, so an
async def gets its real ``param_count`` and ``arity_flags`` rather than the
class branch's (``param_count=0``, ``arity_flags=False,False,False``).

(history: INV-fusus, INV-zudob, INV-bazij)
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from hypergumbo_core.analyze.base import assemble_stable_id
from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.py import _compute_stable_id


def _run_and_load(tmp_path: Path, src: str, filename: str = "models.py") -> dict:
    """Write ``src`` to ``filename`` under ``tmp_path``, run analysis, return JSON."""
    (tmp_path / filename).write_text(src)
    out = tmp_path / "out.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


class TestSameModuleDistinctClassesDistinct:
    """Two classes with distinct bodies in the same module → distinct stable_ids."""

    def test_two_dataclasses_with_different_methods(self, tmp_path: Path) -> None:
        data = _run_and_load(
            tmp_path,
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Symbol:\n"
            "    name: str\n"
            "    def to_dict(self):\n"
            "        return {}\n"
            "\n"
            "@dataclass\n"
            "class Edge:\n"
            "    src: str\n"
            "    dst: str\n"
            "    def to_dict(self):\n"
            "        return {}\n"
            "    def reverse(self):\n"
            "        return None\n",
        )
        classes = {c["name"]: c for c in data["nodes"] if c["kind"] == "class"}
        assert "Symbol" in classes
        assert "Edge" in classes
        assert classes["Symbol"]["stable_id"] != classes["Edge"]["stable_id"], (
            "Symbol and Edge dataclasses share stable_id — distinct class "
            "names must split (v6: name + scope chain in the hash)"
        )

    def test_two_dataclasses_with_different_fields_only(self, tmp_path: Path) -> None:
        """Classes with no methods and different fields get distinct stable_ids
        (the split comes from the class name; fields are not hash inputs)."""
        data = _run_and_load(
            tmp_path,
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class A:\n"
            "    x: int\n"
            "    y: int\n"
            "\n"
            "@dataclass\n"
            "class B:\n"
            "    p: str\n"
            "    q: str\n",
        )
        classes = {c["name"]: c for c in data["nodes"] if c["kind"] == "class"}
        assert classes["A"]["stable_id"] != classes["B"]["stable_id"]

    def test_two_classes_with_different_bases(self, tmp_path: Path) -> None:
        """Same-body classes with distinct bases get distinct stable_ids (the
        split comes from the class name; bases are not hash inputs)."""
        data = _run_and_load(
            tmp_path,
            "class Base1:\n"
            "    pass\n"
            "class Base2:\n"
            "    pass\n"
            "class Child1(Base1):\n"
            "    def go(self): pass\n"
            "class Child2(Base2):\n"
            "    def go(self): pass\n",
        )
        classes = {c["name"]: c for c in data["nodes"] if c["kind"] == "class"}
        assert classes["Child1"]["stable_id"] != classes["Child2"]["stable_id"]

    def test_five_distinct_dataclasses_all_distinct(self, tmp_path: Path) -> None:
        """Five @dataclass classes in one module (the shape of ir.py's), all distinct."""
        data = _run_and_load(
            tmp_path,
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Symbol:\n"
            "    name: str\n"
            "    kind: str\n"
            "@dataclass\n"
            "class Edge:\n"
            "    src: str\n"
            "    dst: str\n"
            "@dataclass\n"
            "class Span:\n"
            "    start_line: int\n"
            "    end_line: int\n"
            "@dataclass\n"
            "class AnalysisRun:\n"
            "    execution_id: str\n"
            "    pass_id: str\n"
            "@dataclass\n"
            "class ExternalRef:\n"
            "    url: str\n",
        )
        classes = [c for c in data["nodes"] if c["kind"] == "class"]
        stable_ids = {c["stable_id"] for c in classes}
        assert len(stable_ids) == 5, (
            f"Expected 5 distinct stable_ids across 5 distinct dataclasses, "
            f"got {len(stable_ids)} (collision groups remain)"
        )


class TestMethodCascadeDistinct:
    """Methods inside distinct classes cascade to distinct stable_ids."""

    def test_same_method_signature_different_containing(self, tmp_path: Path) -> None:
        """to_dict in Symbol vs Edge must have distinct stable_ids."""
        data = _run_and_load(
            tmp_path,
            "from dataclasses import dataclass\n"
            "\n"
            "@dataclass\n"
            "class Symbol:\n"
            "    name: str\n"
            "    def to_dict(self):\n"
            "        return {}\n"
            "\n"
            "@dataclass\n"
            "class Edge:\n"
            "    src: str\n"
            "    dst: str\n"
            "    def to_dict(self):\n"
            "        return {}\n",
        )
        methods = {m["name"]: m for m in data["nodes"] if m["kind"] == "method"}
        sym_td = methods["Symbol.to_dict"]
        edge_td = methods["Edge.to_dict"]
        assert sym_td["stable_id"] != edge_td["stable_id"]

    def test_init_methods_distinct_across_classes(self, tmp_path: Path) -> None:
        """__init__ on different classes must not collide — the most common
        same-signature method across classes."""
        data = _run_and_load(
            tmp_path,
            "class A:\n"
            "    def __init__(self): pass\n"
            "    def alpha(self): pass\n"
            "class B:\n"
            "    def __init__(self): pass\n"
            "    def beta(self): pass\n"
            "class C:\n"
            "    def __init__(self): pass\n"
            "    def gamma(self): pass\n",
        )
        methods = {m["name"]: m for m in data["nodes"] if m["kind"] == "method"}
        sids = {
            methods["A.__init__"]["stable_id"],
            methods["B.__init__"]["stable_id"],
            methods["C.__init__"]["stable_id"],
        }
        assert len(sids) == 3, f"Expected 3 distinct __init__ stable_ids, got {len(sids)}"


class TestSurvivesRenamesAndMoves:
    """The two halves of the Symbol.stable_id docstring promise."""

    def test_class_rename_changes_stable_id(self, tmp_path: Path) -> None:
        """Renaming a class changes its stable_id.

        Per ADR-0014, stable_id is "structural identity within a
        (qualified_name, module_path) scope" — it survives BODY changes but
        NOT rename or move. Shape-only hash inputs (which would survive a
        rename) give every same-shape symbol in a file one stable_id, e.g. all
        zero-parameter bash functions or pytest tests in a file.
        (history: INV-bazij)
        """
        data_old = _run_and_load(
            tmp_path,
            "class OldName:\n"
            "    x: int\n"
            "    def m(self): pass\n",
            filename="mod.py",
        )
        data_new = _run_and_load(
            tmp_path,
            "class NewName:\n"
            "    x: int\n"
            "    def m(self): pass\n",
            filename="mod.py",
        )
        old_cls = next(c for c in data_old["nodes"] if c["kind"] == "class" and c["name"] == "OldName")
        new_cls = next(c for c in data_new["nodes"] if c["kind"] == "class" and c["name"] == "NewName")
        assert old_cls["stable_id"] != new_cls["stable_id"], (
            "Phase 6 PR3 (INV-bazij): renaming a class must change "
            "stable_id — name is now in the hash inputs to prevent the "
            "60% same-file collision pattern."
        )

    def test_method_order_irrelevant(self, tmp_path: Path) -> None:
        """Reordering methods within a class body must not change class stable_id.

        The class hash excludes the member set entirely (no ``body_sig``;
        ADR-0035), so reordering — and indeed any member add/remove — leaves the
        class id unchanged: the "survives body edits" contract. Same-module reorder: rewrites the same file so containing
        identity is held constant; only the body order changes.
        """
        data_a = _run_and_load(
            tmp_path,
            "class C:\n"
            "    def a(self): pass\n"
            "    def b(self): pass\n"
            "    def c(self): pass\n",
            filename="order.py",
        )
        data_b = _run_and_load(
            tmp_path,
            "class C:\n"
            "    def c(self): pass\n"
            "    def a(self): pass\n"
            "    def b(self): pass\n",
            filename="order.py",
        )
        cls_a = next(c for c in data_a["nodes"] if c["kind"] == "class")
        cls_b = next(c for c in data_b["nodes"] if c["kind"] == "class")
        assert cls_a["stable_id"] == cls_b["stable_id"]


class TestSemanticIdentitySplitsByName:
    """Identical-body classes split by name.

    "Same shape means same identity" makes stable_id useless: every
    zero-parameter function or test in a file would share one. Name is part
    of the hash inputs (ADR-0014). (history: INV-bazij)
    """

    def test_two_truly_identical_classes_now_split_by_name(self, tmp_path: Path) -> None:
        """Two same-shape classes in the same file get distinct stable_ids
        because name is part of the hash inputs."""
        data = _run_and_load(
            tmp_path,
            "class A:\n"
            "    x: int\n"
            "    def m(self): pass\n"
            "class B:\n"
            "    x: int\n"
            "    def m(self): pass\n",
        )
        classes = {c["name"]: c for c in data["nodes"] if c["kind"] == "class"}
        assert classes["A"]["stable_id"] != classes["B"]["stable_id"], (
            "Phase 6 PR3 (INV-bazij): two same-shape classes must now "
            "get distinct stable_ids — name is part of the hash inputs."
        )


class TestAsyncFunctionDistinguished:
    """Async functions get their real param_count + arity_flags, not the class branch's."""

    def test_async_and_sync_function_with_same_signature_distinct(self, tmp_path: Path) -> None:
        """An async function and a sync function with the same signature get
        different stable_ids: ``async`` is a hash input (ADR-0035 §1).

        The async def must also take the FUNCTION branch, not the class one:
        a check for ``ast.FunctionDef`` alone is False for ``AsyncFunctionDef``
        and would hash it as a parameterless class.
        """
        sync = ast.parse("def handle(req, resp):\n    pass\n").body[0]
        async_ = ast.parse("async def handle(req, resp):\n    pass\n").body[0]
        assert isinstance(sync, ast.FunctionDef)
        assert isinstance(async_, ast.AsyncFunctionDef)
        sid_sync = _compute_stable_id(sync)
        sid_async = _compute_stable_id(async_)
        assert sid_sync != sid_async
        # The async id is the sync formula plus the marker -- the function
        # branch's parameter count and flags -- never the class branch's.
        assert sid_async == assemble_stable_id(
            "function", 2, "False,False,False", "", "", "", "", 0, is_async=True)
        klass = ast.parse("class handle:\n    pass\n").body[0]
        assert sid_async != _compute_stable_id(klass)

class TestCrossModuleIdenticalSymbolsDistinct:
    """Structurally-identical symbols in different modules must get distinct
    stable_ids — module identity is part of symbol identity. (history: INV-zudob)
    """

    def test_two_identical_classes_in_different_files_distinct(
        self, tmp_path: Path
    ) -> None:
        """Two identical class bodies in distinct files must split stable_id.

        The shape of a test class copied across per-language test modules.
        """
        (tmp_path / "test_ada.py").write_text(
            "class TestAdaAnalysisUnavailable:\n"
            "    def test_fallback(self): pass\n"
        )
        (tmp_path / "test_asm.py").write_text(
            "class TestAdaAnalysisUnavailable:\n"
            "    def test_fallback(self): pass\n"
        )
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())
        classes = [
            c for c in data["nodes"]
            if c["kind"] == "class" and c["name"] == "TestAdaAnalysisUnavailable"
        ]
        assert len(classes) == 2, (
            f"Expected 2 class symbols across 2 files, got {len(classes)}"
        )
        sids = {c["stable_id"] for c in classes}
        assert len(sids) == 2, (
            "Two structurally-identical classes in different modules share "
            "stable_id — containing_stable_id not folded into the top-level "
            "ClassDef hash (INV-zudob)"
        )

    def test_same_class_in_two_test_files_distinct(self, tmp_path: Path) -> None:
        """Same class name, same body shape, two different test modules —
        must be distinct symbols.
        """
        body = (
            "class TestAnalysisRun:\n"
            "    def test_runs(self): pass\n"
            "    def test_completes(self): pass\n"
        )
        (tmp_path / "test_cli.py").write_text(body)
        (tmp_path / "test_runtime.py").write_text(body)
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())
        classes = [
            c for c in data["nodes"]
            if c["kind"] == "class" and c["name"] == "TestAnalysisRun"
        ]
        assert len(classes) == 2
        assert len({c["stable_id"] for c in classes}) == 2

    def test_top_level_function_cross_module_distinct(self, tmp_path: Path) -> None:
        """Top-level functions with unannotated args in different modules must also split.

        Functions take the file identity as their containing scope, just as
        top-level classes do.
        """
        # Unannotated args still normalize to a parameter-name signature, so
        # this takes the typed ``make_typed_stable_id`` tier; the untyped
        # ``_compute_stable_id`` fallback is only for a signature that does
        # not normalize.
        body = "def helper(x, y):\n    return x + y\n"
        (tmp_path / "mod_a.py").write_text(body)
        (tmp_path / "mod_b.py").write_text(body)
        out = tmp_path / "out.json"
        run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
        data = json.loads(out.read_text())
        funcs = [
            f for f in data["nodes"]
            if f["kind"] == "function" and f["name"] == "helper"
        ]
        assert len(funcs) == 2, (
            f"Expected 2 function symbols across 2 files, got {len(funcs)}"
        )
        assert len({f["stable_id"] for f in funcs}) == 2, (
            "Untyped top-level functions with identical bodies in different "
            "modules share stable_id (INV-zudob — function fallback path)"
        )

    def test_class_move_to_different_file_changes_stable_id(
        self, tmp_path: Path
    ) -> None:
        """Moving a class to a different file is a structural change in
        Python's import graph, so its stable_id must change.
        """
        body = (
            "class Handler:\n"
            "    x: int\n"
            "    def run(self): pass\n"
        )
        data_a = _run_and_load(tmp_path, body, filename="pkg_a.py")
        # Remove pkg_a.py so the second analysis only sees pkg_b.py
        (tmp_path / "pkg_a.py").unlink()
        data_b = _run_and_load(tmp_path, body, filename="pkg_b.py")
        cls_a = next(c for c in data_a["nodes"] if c["kind"] == "class")
        cls_b = next(c for c in data_b["nodes"] if c["kind"] == "class")
        assert cls_a["stable_id"] != cls_b["stable_id"], (
            "Class moved to a different module path retained its stable_id "
            "— containing identity not folded into the hash"
        )
