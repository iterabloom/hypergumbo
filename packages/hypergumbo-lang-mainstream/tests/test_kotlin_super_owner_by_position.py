# SPDX-License-Identifier: AGPL-3.0-or-later
"""``super.m()`` walks the bases of the class that contains it (WI-gikuk).

INV-dupol resolves ``super.m()`` through the enclosing class's
``base_classes``. The enclosing class was looked up by its bare NAME in a
run-wide table that keeps one class per name, so in ``a.Foo : BaseA`` and
``b.Foo : BaseB`` both ``super.m()`` calls walked whichever ``Foo``
registered last and reached the same base. The class is now the one whose
declaration node contains the call (by POSITION, core
``enclosing_declared_symbol``), and the base's ``m`` is the one at the path
the file imports (WI-tipoh's ``_kt_import_target``), not whichever ``Base.m``
the name-keyed resolver kept.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/kotlin.py
from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_mainstream.kotlin import analyze_kotlin

_SUB = (
    "package {pkg}\n\nimport {base_pkg}.{base}\n\n"
    "class {cls} : {base}() {{\n    override fun m() {{\n        super.m()\n    }}\n}}\n"
)


def _super_targets(root: Path) -> dict[str, str]:
    """``{caller file: callee qualified name}`` for every resolved ``super.m()``."""
    result = analyze_kotlin(root)
    by_id = {s.id: s for s in result.symbols}
    out = {}
    for e in result.edges:
        if e.edge_type == "calls" and e.evidence_type == "ast_call_this":
            out[Path(by_id[e.src].path).name] = by_id[e.dst].qualified_name
    return out


def test_super_walks_the_bases_of_its_own_class(tmp_path: Path) -> None:
    (tmp_path / "bases.kt").write_text(
        "package p\n\nopen class BaseA {\n    open fun m() {}\n}\n\n"
        "open class BaseB {\n    open fun m() {}\n}\n")
    (tmp_path / "a.kt").write_text(_SUB.format(pkg="a", base_pkg="p", base="BaseA", cls="Foo"))
    (tmp_path / "b.kt").write_text(_SUB.format(pkg="b", base_pkg="p", base="BaseB", cls="Foo"))
    assert _super_targets(tmp_path) == {"a.kt": "p.BaseA.m", "b.kt": "p.BaseB.m"}


def test_super_reaches_the_imported_base_not_a_namesake(tmp_path: Path) -> None:
    for pkg in ("x", "y"):
        (tmp_path / f"{pkg}.kt").write_text(
            f"package {pkg}\n\nopen class Base {{\n    open fun m() {{}}\n}}\n")
    (tmp_path / "a.kt").write_text(_SUB.format(pkg="a", base_pkg="x", base="Base", cls="Foo"))
    (tmp_path / "b.kt").write_text(_SUB.format(pkg="b", base_pkg="y", base="Base", cls="Bar"))
    assert _super_targets(tmp_path) == {"a.kt": "x.Base.m", "b.kt": "y.Base.m"}


def test_two_nested_classes_of_one_name_in_one_file_keep_their_bases(
    tmp_path: Path,
) -> None:
    """Both nested classes are emitted as ``Inner``: one entry by name."""
    (tmp_path / "n.kt").write_text(
        "package n\n\nopen class BaseA {\n    open fun m() {}\n}\n\n"
        "open class BaseB {\n    open fun m() {}\n}\n\n"
        "class O1 {\n    class Inner : BaseA() {\n"
        "        override fun m() {\n            super.m()\n        }\n    }\n}\n\n"
        "class O2 {\n    class Inner : BaseB() {\n"
        "        override fun m() {\n            super.m()\n        }\n    }\n}\n")
    result = analyze_kotlin(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    pairs = {
        (by_id[e.src].qualified_name, by_id[e.dst].qualified_name)
        for e in result.edges
        if e.edge_type == "calls" and e.evidence_type == "ast_call_this"
    }
    assert pairs == {("n.O1.Inner.m", "n.BaseA.m"), ("n.O2.Inner.m", "n.BaseB.m")}


def test_this_and_callable_references_bind_the_own_class_member(tmp_path: Path) -> None:
    """``this.helper()``, ``::helper`` and ``this::helper`` in ``b.Foo`` are b's."""
    body = (
        "class Foo {{\n    fun helper() {{}}\n    fun m() {{\n"
        "        this.helper()\n        val f = ::helper\n        val g = this::helper\n"
        "    }}\n}}\n"
    )
    (tmp_path / "a.kt").write_text("package a\n\n" + body.format())
    (tmp_path / "b.kt").write_text("package b\n\n\n\n" + body.format())
    result = analyze_kotlin(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    edges = [
        e for e in result.edges
        if e.edge_type in ("calls", "references") and e.dst in by_id
        and by_id[e.dst].name == "Foo.helper"
    ]
    kinds = sorted((Path(by_id[e.src].path).name, e.evidence_type) for e in edges)
    assert kinds == sorted(
        (f, ev) for f in ("a.kt", "b.kt")
        for ev in ("ast_call_this", "callable_reference", "callable_reference")
    )
    for e in edges:
        assert by_id[e.dst].path == by_id[e.src].path, (e.src, e.dst)


def test_a_base_of_the_child_package_needs_no_import(tmp_path: Path) -> None:
    """``a.Base`` and ``b.Base``: each package's ``Foo : Base()`` is its own."""
    for pkg in ("a", "b"):
        (tmp_path / pkg).mkdir()
        (tmp_path / pkg / "Base.kt").write_text(
            f"package {pkg}\n\nopen class Base {{\n    open fun m() {{}}\n}}\n")
        (tmp_path / pkg / "Foo.kt").write_text(
            f"package {pkg}\n\nclass Foo : Base() {{\n"
            "    override fun m() {\n        super.m()\n    }\n}\n")
    result = analyze_kotlin(tmp_path)
    by_id = {s.id: s for s in result.symbols}
    pairs = {
        (e.edge_type, by_id[e.src].qualified_name, by_id[e.dst].qualified_name)
        for e in result.edges
        if e.edge_type in ("extends", "calls") and e.src in by_id and e.dst in by_id
    }
    assert pairs == {
        ("extends", "a.Foo", "a.Base"), ("extends", "b.Foo", "b.Base"),
        ("calls", "a.Foo.m", "a.Base.m"), ("calls", "b.Foo.m", "b.Base.m"),
    }
