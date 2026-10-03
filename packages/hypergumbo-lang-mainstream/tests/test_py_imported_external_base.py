# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-ratid: a Python base named THROUGH AN IMPORT of an out-of-tree module is
external, whatever in-repo class shares its last name segment.

Before this item, a base class was bound by NAME alone:

* a dotted base (``class SimpleTestCase(unittest.TestCase)``) was deferred by
  ``py.py`` to the core inheritance linker, which has no import context and
  retried the LAST segment (``TestCase``) against every in-repo class. On the
  Django repository that bound Django's ``SimpleTestCase`` to Django's OWN
  ``TestCase`` (same file), closing an inheritance cycle
  ``TestCase -> TransactionTestCase -> SimpleTestCase -> TestCase``; the
  ``inherited_calls`` C3 walk refuses a cyclic hierarchy, so no inherited
  method resolved for any Django test class;
* a bare base imported from an external module
  (``from unittest import TestCase; class Plain(TestCase)``) was bound by
  ``py.py``'s own ``_resolve_base_class`` to the in-repo ``TestCase`` whenever
  that was the only class of that name: the import was consulted only to break
  a tie between several in-repo candidates.

The rule now (LIVE.md §3: resolve by the import / qualified name, never by the
bare name): ``py.py`` resolves the base expression's ROOT through the file's
imports. If that names a module that is not in the tree, the base is external
(``python:<module>:0-0:<Name>:unresolved`` with an ``ExternalRef``), and the
core linker does not re-resolve a base the analyzer already classified as
external. An import of an in-tree module, a root that is not import-bound, and
a root shadowed by a class defined in the same file keep the previous
resolution.

Every collision fixture asserts BOTH sides (LIVE.md §1 rule 6): the external
edge exists AND no resolved edge to the in-repo namesake exists.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_lang_mainstream.py import analyze_python

# A miniature of django/test/testcases.py: the in-repo ``TestCase`` sits BELOW
# ``SimpleTestCase``, whose own base is the stdlib ``unittest.TestCase``.
_TESTCASES = (
    "import unittest\n"
    "\n"
    "\n"
    "class SimpleTestCase(unittest.TestCase):\n"
    "    def assertThing(self):\n"
    "        return 1\n"
    "\n"
    "\n"
    "class TransactionTestCase(SimpleTestCase):\n"
    "    pass\n"
    "\n"
    "\n"
    "class TestCase(TransactionTestCase):\n"
    "    pass\n"
)

_TESTS = (
    "from pkg.testcases import TestCase\n"
    "\n"
    "\n"
    "class Base(TestCase):\n"
    "    def helper(self):\n"
    "        return 1\n"
    "\n"
    "\n"
    "class MyTests(Base):\n"
    "    def test_a(self):\n"
    "        self.helper()\n"
    "        self.assertThing()\n"
)


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _behavior_map(tmp_path: Path, files: dict[str, str]) -> dict:
    from hypergumbo_core.cli import run_behavior_map

    _write(tmp_path, files)
    out_path = tmp_path / "out.json"
    run_behavior_map(
        repo_root=tmp_path, out_path=out_path, include_sketch_precomputed=False,
    )
    return json.loads(out_path.read_text())


def _class_id(data: dict, name: str, path_suffix: str) -> str:
    ids = [
        n["id"] for n in data["nodes"]
        if n.get("name") == name and n.get("kind") == "class"
        and n.get("path", "").endswith(path_suffix)
    ]
    assert len(ids) == 1, (name, path_suffix, ids)
    return ids[0]


def _extends_from(data: dict, src: str) -> list[dict]:
    return [
        e for e in data["edges"]
        if e["type"] in ("extends", "implements") and e["src"] == src
    ]


def _django_like(tmp_path: Path, extra: dict[str, str] | None = None) -> dict:
    files = {
        "pkg/__init__.py": "",
        "pkg/testcases.py": _TESTCASES,
        "pkg/tests.py": _TESTS,
    }
    files.update(extra or {})
    return _behavior_map(tmp_path, files)


class TestDottedExternalBase:
    def test_dotted_stdlib_base_does_not_bind_in_repo_namesake(
        self, tmp_path: Path,
    ) -> None:
        data = _django_like(tmp_path)
        simple = _class_id(data, "SimpleTestCase", "pkg/testcases.py")
        in_repo = _class_id(data, "TestCase", "pkg/testcases.py")
        edges = _extends_from(data, simple)
        assert [e for e in edges if e["dst"] == in_repo] == [], edges
        assert len(edges) == 1, edges
        assert edges[0]["is_resolved"] is False
        assert edges[0]["dst"] == "python:unittest:0-0:TestCase:external_symbol"

    def test_aliased_module_root_is_resolved_through_the_alias(
        self, tmp_path: Path,
    ) -> None:
        data = _django_like(tmp_path, {"pkg/other.py": (
            "import unittest as ut\n"
            "class Other(ut.TestCase):\n"
            "    pass\n"
        )})
        other = _class_id(data, "Other", "pkg/other.py")
        in_repo = _class_id(data, "TestCase", "pkg/testcases.py")
        edges = _extends_from(data, other)
        assert [e for e in edges if e["dst"] == in_repo] == [], edges
        assert [e["dst"] for e in edges] == [
            "python:unittest:0-0:TestCase:external_symbol",
        ]

    def test_dotted_base_through_from_imported_module(
        self, tmp_path: Path,
    ) -> None:
        # ``from django.db import models; class M(models.Model)`` in a project
        # that does not contain Django but defines its own ``Model``.
        data = _behavior_map(tmp_path, {
            "app/__init__.py": "",
            "app/base.py": "class Model:\n    pass\n",
            "app/models.py": (
                "from django.db import models\n"
                "class Ticket(models.Model):\n"
                "    pass\n"
            ),
        })
        ticket = _class_id(data, "Ticket", "app/models.py")
        in_repo = _class_id(data, "Model", "app/base.py")
        edges = _extends_from(data, ticket)
        assert [e for e in edges if e["dst"] == in_repo] == [], edges
        assert [e["dst"] for e in edges] == [
            "python:django.db.models:0-0:Model:external_symbol",
        ]


class TestBareImportedExternalBase:
    def test_bare_imported_base_does_not_bind_single_in_repo_namesake(
        self, tmp_path: Path,
    ) -> None:
        data = _django_like(tmp_path, {"pkg/bare.py": (
            "from unittest import TestCase\n"
            "class Plain(TestCase):\n"
            "    pass\n"
        )})
        plain = _class_id(data, "Plain", "pkg/bare.py")
        in_repo = _class_id(data, "TestCase", "pkg/testcases.py")
        edges = _extends_from(data, plain)
        assert [e for e in edges if e["dst"] == in_repo] == [], edges
        assert [e["dst"] for e in edges] == [
            "python:unittest:0-0:TestCase:external_symbol",
        ]

    def test_aliased_bare_import_uses_original_name(
        self, tmp_path: Path,
    ) -> None:
        data = _django_like(tmp_path, {"pkg/alias.py": (
            "from unittest import TestCase as UTC\n"
            "class Aliased(UTC):\n"
            "    pass\n"
        )})
        aliased = _class_id(data, "Aliased", "pkg/alias.py")
        assert [e["dst"] for e in _extends_from(data, aliased)] == [
            "python:unittest:0-0:TestCase:external_symbol",
        ]

    def test_shadowing_class_that_extends_the_import(
        self, tmp_path: Path,
    ) -> None:
        # ``class TestCase(TestCase)``: the base is evaluated before the name
        # is rebound, so it is the imported one. The class itself is the only
        # same-file class of that name and must not count as a shadow.
        data = _django_like(tmp_path, {"pkg/shadow.py": (
            "from unittest import TestCase\n"
            "class TestCase(TestCase):\n"
            "    pass\n"
        )})
        shadow = _class_id(data, "TestCase", "pkg/shadow.py")
        in_repo = _class_id(data, "TestCase", "pkg/testcases.py")
        edges = _extends_from(data, shadow)
        assert [e for e in edges if e["dst"] == in_repo] == [], edges
        assert [e["dst"] for e in edges] == [
            "python:unittest:0-0:TestCase:external_symbol",
        ]


class TestInTreeControls:
    """The rule must not cost an in-tree base its resolved edge."""

    def test_bare_import_of_in_tree_class_still_resolves(
        self, tmp_path: Path,
    ) -> None:
        data = _django_like(tmp_path)
        base = _class_id(data, "Base", "pkg/tests.py")
        in_repo = _class_id(data, "TestCase", "pkg/testcases.py")
        edges = _extends_from(data, base)
        assert [(e["dst"], e["is_resolved"]) for e in edges] == [(in_repo, True)]

    def test_dotted_in_tree_module_base_still_resolves(
        self, tmp_path: Path,
    ) -> None:
        data = _django_like(tmp_path, {"pkg/dotted.py": (
            "import pkg.testcases\n"
            "class Dotted(pkg.testcases.TestCase):\n"
            "    pass\n"
        )})
        dotted = _class_id(data, "Dotted", "pkg/dotted.py")
        in_repo = _class_id(data, "TestCase", "pkg/testcases.py")
        edges = _extends_from(data, dotted)
        assert [(e["dst"], e["is_resolved"]) for e in edges] == [(in_repo, True)]

    def test_same_file_class_shadowing_the_import_keeps_local_resolution(
        self, tmp_path: Path,
    ) -> None:
        data = _django_like(tmp_path, {"pkg/local.py": (
            "from unittest import TestCase\n"
            "class TestCase:\n"
            "    pass\n"
            "class Local(TestCase):\n"
            "    pass\n"
        )})
        local = _class_id(data, "Local", "pkg/local.py")
        local_tc = _class_id(data, "TestCase", "pkg/local.py")
        edges = _extends_from(data, local)
        assert [(e["dst"], e["is_resolved"]) for e in edges] == [(local_tc, True)]

    def test_nested_class_of_imported_in_tree_class_is_not_external(
        self, tmp_path: Path,
    ) -> None:
        # ``OrderSerializer.Meta``: the import binds a CLASS of an in-tree
        # module, so the qualified name's "module" is not a module at all.
        # Declaring it external would mint a workspace-prefixed phantom
        # (INV-nuzas). It keeps the previous resolution, the linker's last
        # segment, which on this fixture finds the class itself and emits
        # nothing (a pre-existing gap of in-tree dotted bases, not this rule).
        data = _behavior_map(tmp_path, {
            "pkg/__init__.py": "",
            "pkg/ser.py": (
                "class OrderSerializer:\n"
                "    class Meta:\n"
                "        fields = 1\n"
            ),
            "pkg/sub.py": (
                "from pkg.ser import OrderSerializer\n"
                "class SubSerializer(OrderSerializer):\n"
                "    class Meta(OrderSerializer.Meta):\n"
                "        pass\n"
            ),
        })
        sub_meta = [
            n["id"] for n in data["nodes"]
            if n.get("kind") == "class" and n.get("path", "").endswith("pkg/sub.py")
            and n.get("name", "").endswith("Meta")
        ]
        assert len(sub_meta) == 1, sub_meta
        edges = _extends_from(data, sub_meta[0])
        assert [e for e in edges if not e["is_resolved"]] == [], edges
        ext_ids = [
            n["id"] for n in data["nodes"] if n.get("kind") == "external_symbol"
        ]
        assert not any("pkg" in i for i in ext_ids), ext_ids

    def test_builtin_base_unchanged(self, tmp_path: Path) -> None:
        data = _behavior_map(tmp_path, {"m.py": "class E(Exception):\n    pass\n"})
        e_id = _class_id(data, "E", "m.py")
        assert [e["dst"] for e in _extends_from(data, e_id)] == [
            "python:external:0-0:Exception:external_symbol",
        ]


class TestNoCycleSoInheritedCallsResolve:
    """The behaviour the cycle cost: the C3 walk resolves inherited methods
    for every class under the formerly cyclic hierarchy."""

    def test_inherited_methods_resolve_under_the_test_hierarchy(
        self, tmp_path: Path,
    ) -> None:
        data = _django_like(tmp_path)
        test_a = [
            n["id"] for n in data["nodes"]
            if n.get("name") == "MyTests.test_a" and n.get("kind") == "method"
        ]
        assert len(test_a) == 1, test_a
        names = {n["id"]: n.get("name") for n in data["nodes"]}
        resolved = sorted(
            names.get(e["dst"]) for e in data["edges"]
            if e["type"] == "calls" and e["src"] == test_a[0]
            and e.get("is_resolved")
        )
        assert resolved == ["Base.helper", "SimpleTestCase.assertThing"], resolved


class TestAnalyzerAloneEmitsTheExternalEdge:
    """Reach check at the analyzer (before any linker runs): the dotted base
    is classified by ``py.py`` itself, not left for the linker."""

    def test_dotted_base_external_edge_carries_external_ref(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, {
            "pkg/__init__.py": "",
            "pkg/testcases.py": _TESTCASES,
        })
        res = analyze_python(tmp_path)
        simple = [s for s in res.symbols if s.name == "SimpleTestCase"]
        assert len(simple) == 1
        edges = [
            e for e in res.edges
            if e.edge_type == "extends" and e.src == simple[0].id
        ]
        assert len(edges) == 1, edges
        assert edges[0].is_resolved is False
        assert edges[0].dst == "python:unittest:0-0:TestCase:unresolved"
        assert edges[0].dst_ref is not None
        assert (edges[0].dst_ref.module_path, edges[0].dst_ref.name) == (
            "unittest", "TestCase",
        )


class TestHelpers:
    """Direct coverage of the WI-ratid helpers' arms the fixtures above do not
    reach."""

    def test_reverse_suffix_module_is_in_tree(self) -> None:
        # ``foo.bar`` imported where the in-tree key is ``src.foo.bar`` (source
        # root not detected): a suffix of an in-tree key is in-tree.
        from hypergumbo_lang_mainstream.py import (
            _base_module_is_in_tree,
            _module_key_suffixes,
        )
        keys = frozenset({"src.foo.bar"})
        suffixes = _module_key_suffixes(keys)
        assert suffixes == frozenset({"src.foo.bar", "foo.bar", "bar"})
        assert _base_module_is_in_tree("foo.bar", "X", keys, suffixes) is True
        assert _base_module_is_in_tree("foo.bar", "X", keys) is False

    def test_reverse_suffix_submodule_is_in_tree(self) -> None:
        # ``from foo import bar`` + ``bar.X`` where the key is ``src.foo.bar``.
        from hypergumbo_lang_mainstream.py import (
            _base_module_is_in_tree,
            _module_key_suffixes,
        )
        keys = frozenset({"src.foo.bar"})
        suffixes = _module_key_suffixes(keys)
        assert _base_module_is_in_tree("foo", "bar", keys, suffixes) is True
        assert _base_module_is_in_tree("foo", "bar", keys) is False

    def test_import_target_in_tree_tests_every_prefix(self) -> None:
        from hypergumbo_lang_mainstream.py import (
            _import_target_in_tree,
            _module_key_suffixes,
        )
        keys = frozenset({"pkg.ser"})
        suffixes = _module_key_suffixes(keys)
        # A class of an in-tree module, used as a namespace.
        assert _import_target_in_tree("pkg.ser.OrderSerializer", "Meta", keys, suffixes)
        # The stdlib.
        assert not _import_target_in_tree("unittest", "TestCase", keys, suffixes)

    def test_import_qualified_base_shapes(self) -> None:
        from hypergumbo_lang_mainstream.py import _import_qualified_base
        imports = {"UTC": ("unittest", "TestCase"), "models": ("django.db", "models")}
        module_imports = {"unittest": "unittest", "ut": "unittest", "abc": "abc"}
        assert _import_qualified_base("unittest.TestCase", imports, module_imports) == (
            "unittest", "TestCase",
        )
        assert _import_qualified_base("ut.TestCase", imports, module_imports) == (
            "unittest", "TestCase",
        )
        assert _import_qualified_base("UTC", imports, module_imports) == (
            "unittest", "TestCase",
        )
        assert _import_qualified_base("models.Model", imports, module_imports) == (
            "django.db.models", "Model",
        )
        # Not import-bound.
        assert _import_qualified_base("Outer.Inner", imports, module_imports) is None
        # A bare MODULE as the base: no class segment to name.
        assert _import_qualified_base("abc", imports, module_imports) is None
