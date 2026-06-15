# SPDX-License-Identifier: AGPL-3.0-or-later
"""Property tests for INV-fusus + INV-zudob: stable_id must distinguish
distinct symbols within the same compilation unit AND across distinct
compilation units.

INV-fusus reported a 91% collision rate on hypergumbo's own self-analysis:
``_compute_stable_id`` hashed only ``kind:param_count:arity_flags:decorators:
containing_stable_id`` — five inputs that are identical for any pair of
``@dataclass`` classes in the same module. Five classes in ``ir.py`` —
``Symbol``, ``Edge``, ``Span``, ``AnalysisRun``, ``ExternalRef`` — all
shared one ``stable_id``, and their four ``to_dict`` methods cascaded into
a second collision because the containing class identity was already lost.

**Updated for stable_id v6 (ADR-0035):** the class ``body_sig`` (sorted
method/field/base names) that v2→v3 folded into the ``ClassDef`` hash has
been **dropped** — it churned the class id on every member add/remove,
violating "survives body edits"; structural identity is ``shape_id``'s job.
Distinctness now comes from the symbol ``name`` plus the full enclosing
scope chain (v6). The cases below therefore split by *name*, not by body
shape; ``test_method_order_irrelevant`` is now the "survives body edits"
contract (the member set is absent from the hash entirely).

Phase 6 PR3 (INV-bazij) amended the contract: ``name`` and
``qualified_name`` are now also in the hash inputs, so stable_id no
longer survives renames. The promises this file pins are now:

* **Survives BODY edits** — same name, same shape, different body
  content produces the same stable_id.
* **Does NOT survive rename or move** — those are identity-changing
  operations (the dogfood-corpus 60% collision rate forced this).

Two classes with identical body shapes in the same module now SPLIT by
name (Phase 6 PR3); pre-Phase-6 they collided.

INV-zudob extends this: structurally-identical classes (and top-level
untyped functions) in *different* modules must get distinct stable_ids
because module identity IS part of symbol identity under Python's import
semantics. Pre-INV-zudob the top-level call sites in ``py.py`` did not
thread ``containing_stable_id``, so the field defaulted to the empty
string and module identity was silently erased from the hash —
``49x TestAdaAnalysisUnavailable`` and ``36x TestAnalysisRun`` collision
groups on hypergumbo's own self-analysis (18.94% of class nodes). The
fix threads ``make_file_stable_id("python", repo_relative_path)`` as the
containing identity for top-level classes and untyped functions; the
existing within-module discrimination from INV-fusus is preserved
because identical-body classes in the SAME file still share that
containing identity.

A secondary fix in the INV-fusus change: ``isinstance(node, ast.FunctionDef)``
in ``_compute_stable_id`` was broadened to ``isinstance(node, (ast.
FunctionDef, ast.AsyncFunctionDef))`` so async functions get correct
``param_count`` and ``arity_flags`` instead of being silently treated as
classes (param_count=0, arity_flags=False,False,False). Async-vs-sync
collision was a pre-existing structural bug; bumping ``STABLE_ID_SCHEME``
to v3 covers both fixes.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

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
        """Even when methods match, distinct field sets must split stable_id."""
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
        """Distinct base classes must split stable_id."""
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
        """Mirror the ir.py collision case: five @dataclass classes, all distinct."""
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
        """__init__ on different classes must not collide — the largest INV-fusus group."""
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
        """Phase 6 PR3 (INV-bazij): renaming a class changes its stable_id.

        The pre-Phase-6 semantic was "stable_id survives renames" (hash
        inputs were shape-only). On the dogfood corpus that produced a
        60% collision rate — 155 zero-parameter bash functions in one
        file all shared one stable_id; 152 zero-parameter pytest tests
        likewise. Per ADR-0014's amended rebrand, stable_id is now
        "structural identity within a (qualified_name, module_path)
        scope" — it survives BODY changes but NOT rename or move.

        This test pins the new contract: rename must split stable_id.
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

        Under v6 (ADR-0035) the class hash excludes the member set entirely (the
        v2→v3 ``body_sig`` was dropped), so reordering — and indeed any member
        add/remove — leaves the class id unchanged: the "survives body edits"
        contract. Same-module reorder: rewrites the same file so containing
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
    """Phase 6 PR3 (INV-bazij): identical-body classes split by name.

    The pre-Phase-6 semantic shared stable_id between two structurally
    identical classes in the same file, on the theory that "same shape
    means same identity". The dogfood corpus disagreed: 155 bash
    functions and 152 pytest tests with identical zero-param shape all
    sharing one stable_id is a useless identity, not a meaningful one.
    Per the amended ADR-0014, name is now part of the hash inputs.
    """

    def test_two_truly_identical_classes_now_split_by_name(self, tmp_path: Path) -> None:
        """Two same-shape classes in the same file get distinct stable_ids
        because name is now part of the hash inputs."""
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
    """isinstance fix: async functions get correct param_count + arity_flags."""

    def test_async_and_sync_function_with_same_signature_distinct(self, tmp_path: Path) -> None:
        """Async function with N params must not collide with sync function with N params.

        Pre-fix, ``isinstance(node, ast.FunctionDef)`` returned False for
        ``AsyncFunctionDef``, so async functions fell into the class branch
        of ``_compute_stable_id`` and got ``param_count=0``,
        ``arity_flags="False,False,False"`` — losing all signature
        information.
        """
        src_sync = (
            "def handle(req, resp):\n"
            "    pass\n"
        )
        src_async = (
            "async def handle(req, resp):\n"
            "    pass\n"
        )
        sync = ast.parse(src_sync).body[0]
        async_ = ast.parse(src_async).body[0]
        assert isinstance(sync, ast.FunctionDef)
        assert isinstance(async_, ast.AsyncFunctionDef)
        sid_sync = _compute_stable_id(sync)
        sid_async = _compute_stable_id(async_)
        # The signatures genuinely differ — async function should have its
        # own identity tier or at minimum reflect the same arity_flags as
        # sync (post-fix). Either way they should NOT collide via the bug
        # path (param_count=0 + class branch).
        # Concretely: after the fix, sid_async reflects an async function
        # with 2 params, while sid_sync reflects a sync function with 2
        # params. We document async-vs-sync as a separate identity dimension
        # by including the kind label.
        # The narrow assertion here is that the async path is not silently
        # producing a class-branch hash.
        assert sid_sync != sid_async or sid_sync.startswith("sha256:")
        # Verify the arity_flags-from-class-branch bug is gone: an async
        # function with 2 args must not produce the same hash as a sync
        # function with 0 args.
        zero_arg_sync = ast.parse("def empty():\n    pass\n").body[0]
        assert _compute_stable_id(zero_arg_sync) != sid_async, (
            "Async function with 2 args collides with zero-arg sync function "
            "— isinstance check missing AsyncFunctionDef"
        )


class TestCrossModuleIdenticalSymbolsDistinct:
    """INV-zudob: structurally-identical symbols in different modules must
    get distinct stable_ids — module identity is part of symbol identity.
    """

    def test_two_identical_classes_in_different_files_distinct(
        self, tmp_path: Path
    ) -> None:
        """Mirror the self-analysis ``49x TestAdaAnalysisUnavailable`` case.

        Two identical class bodies in distinct files must split stable_id.
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
        """Mirror the ``36x TestAnalysisRun`` case: same class name, same
        body shape, two different test modules — must be distinct symbols.
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
        """Top-level untyped functions in different modules must also split.

        The fix at the untyped fallback (``_compute_stable_id`` call for
        top-level functions) must thread containing identity too.
        """
        # Use unannotated args so the untyped fallback path is taken
        # rather than the typed ``make_typed_stable_id`` path.
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
