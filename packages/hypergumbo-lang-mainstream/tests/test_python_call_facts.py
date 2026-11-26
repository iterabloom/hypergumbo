# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the Python analyzer binds itself and what it reports (ADR-0064, batch 5b).

An import names a FILE of the tree, exactly, or nothing of it
(``_ModuleFiles``): the module roots are the deepest source root, every
``src`` directory and the repository root, and the importer's own
``sys.path`` directory comes first. The dotted-suffix match that bound
``from json import dumps`` to an in-tree ``pkg/json.py`` is gone from the
calls, and so is the base-class pick of a namesake by one holder, by path or
by id.

Each file hands over its table (``ImportScope``, a table of files). Every
call the analyzer does not bind goes out through ``make_unresolved_edge``
with the facts the call site has:

* ``mod.f()`` / ``name.m()`` / ``root.a.m()`` on an import: ``qualifier`` as
  written;
* ``self.m()``: ``enclosing_class``; ``super().m()``: ``enclosing_class`` and
  ``super_receiver``; ``self.field.m()`` on an inherited field:
  ``inherited_field_receiver``;
* a typed receiver: ``receiver_type_hint``;
* a bare call: no fact (the shared pass reads the table by the name).

The first part pins the table and each route at the analyzer; the second runs
the whole pipeline with a NAMESAKE beside each target, so that a bind by name
would show as wrong or as a tie.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.import_scope import MODULES_ARE_FILES
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.py import (
    _ModuleFiles,
    _detect_source_roots,
    _extract_file_analysis,
    _import_scope,
    analyze_python,
)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _analyze(root: Path, files: dict[str, str]):
    _write(root, files)
    return analyze_python(root)


def _edges(result, src_path: str) -> list[Edge]:
    return [
        e for e in result.edges
        if e.edge_type in ("calls", "instantiates")
        and e.src.split(":")[1].endswith(f"/{src_path}")
    ]


def _stub(edges: list[Edge], callee: str, line: int | None = None) -> Edge:
    """The one unresolved stub of ``callee`` (on ``line``)."""
    found = [
        e for e in edges if not e.is_resolved and (e.meta or {}).get("callee_name") == callee
        and (line is None or e.line == line)
    ]
    assert len(found) == 1, [(e.line, e.dst, e.meta) for e in edges]
    return found[0]


def _facts(edge: Edge) -> dict:
    return {k: v for k, v in (edge.meta or {}).items()
            if k not in ("callee_name", "access_mode", "io_mode", "call_arg_shape")}


def _resolved_to(edges: list[Edge], line: int) -> list[str]:
    return [e.dst for e in edges if e.is_resolved and e.line == line]


# --- the module a dotted name names -------------------------------------------


def _modules(root: Path, files: dict[str, str]) -> _ModuleFiles:
    _write(root, files)
    roots = _detect_source_roots(root)
    analyses = {}
    for rel in files:
        analysis, _ = _extract_file_analysis(root / rel, root, roots)
        assert analysis is not None
        analyses[root / rel] = analysis
    return _ModuleFiles(analyses, root, roots, {})


def test_a_module_names_one_file_under_the_roots_or_nothing(tmp_path: Path) -> None:
    modules = _modules(tmp_path, {
        "src/poetry/core.py": "def f(): pass\n",           # a namespace package under src/
        "pkg/__init__.py": "",
        "pkg/json.py": "def dumps(x): pass\n",
        "a/src/dup/util.py": "", "b/src/dup/util.py": "",   # two src dirs: a tie
        "backend/app/__init__.py": "", "backend/app/crud.py": "",
    })
    importer = tmp_path / "pkg" / "json.py"
    assert modules.file_for("poetry.core", importer) == tmp_path / "src/poetry/core.py"
    assert modules.file_for("src.poetry.core", importer) == tmp_path / "src/poetry/core.py"
    assert modules.file_for("pkg", importer) == tmp_path / "pkg/__init__.py"
    assert modules.file_for("pkg.json", importer) == tmp_path / "pkg/json.py"
    # No dotted-suffix match: ``json`` is a library's, ``app.crud`` names no root's file.
    assert modules.file_for("json", importer) is None
    assert modules.file_for("app.crud", importer) is None
    assert modules.file_for("dup.util", importer) is None
    assert modules.file_for("", importer) is None
    assert modules.symbol("pkg.json", "dumps", importer) is not None
    assert modules.symbol("pkg.json", "<module>", importer) is None


def test_a_test_files_own_directory_comes_first(tmp_path: Path) -> None:
    modules = _modules(tmp_path, {
        "tests/helpers.py": "def h(): pass\n",
        "tests/test_a.py": "from helpers import h\n",
        "helpers.py": "def h(): pass\n",
    })
    test_file = tmp_path / "tests/test_a.py"
    assert modules.file_for("helpers", test_file) == tmp_path / "tests/helpers.py"
    # A file that is no test and no script does not get its directory on the path.
    assert modules.file_for("helpers", tmp_path / "helpers.py") == tmp_path / "helpers.py"


def test_a_name_is_followed_through_what_a_package_passes_on(tmp_path: Path) -> None:
    modules = _modules(tmp_path, {
        "pkg/__init__.py": "from .impl import Thing\nfrom .gone import Missing\n",
        "pkg/impl.py": "class Thing: pass\n",
    })
    importer = tmp_path / "main.py"
    found = modules.symbol("pkg", "Thing", importer)
    assert found is not None and found.path.endswith("pkg/impl.py")
    assert modules.symbol("pkg", "Missing", importer) is None
    assert modules.symbol("pkg", "Absent", importer) is None


# --- the table ------------------------------------------------------------------


_TABLE_TREE = {
    "pkg/__init__.py": "from .impl import Thing\nhelper = 1\n",
    "pkg/impl.py": "class Thing: pass\n",
    "pkg/sub.py": "def f(): pass\n",
    "pkg/helper.py": "def g(): pass\n",
    "pkg/listed.py": "__all__ = ['a', 'b']\ndef a(): pass\ndef b(): pass\n",
    "pkg/open.py": "def c(): pass\n",
    "main.py": (
        "import pkg.sub\nimport pkg.impl as pi\nimport os.path\nimport json as j\n"
        "from pkg import sub as s, Thing, helper\nfrom pkg.impl import Thing as T\n"
        "from pkg.listed import *\nfrom pkg.open import *\nfrom os import sep\n"
        "from pkg.nothere import *\n"
        "def later():\n    from pkg.sub import f\n"
    ),
    "pkg/rel.py": "from . import sub\nfrom .impl import Thing\nfrom .. import beyond\n",
}


def test_every_kind_of_import_is_in_the_table_as_a_file(tmp_path: Path) -> None:
    result = _analyze(tmp_path, _TABLE_TREE)
    scope = result.import_scopes[str(tmp_path / "main.py")]
    assert scope.module_kind == MODULES_ARE_FILES
    assert not scope.library_names_screened
    assert scope.namespace("pkg") == "pkg/__init__.py"
    assert scope.namespace("pkg.sub") == "pkg/sub.py"
    assert scope.namespace("pi") == "pkg/impl.py"
    assert scope.namespace("s") == "pkg/sub.py"
    assert scope.namespace("os") is None and scope.namespace("j") is None
    thing = scope.named("Thing")
    assert thing is not None and (thing.module, thing.name) == ("pkg/__init__.py", "Thing")
    aliased = scope.named("T")
    assert aliased is not None and (aliased.module, aliased.name) == ("pkg/impl.py", "Thing")
    # ``pkg`` declares ``helper`` itself: the attribute, not the submodule.
    bound = scope.named("helper")
    assert bound is not None and bound.module == "pkg/__init__.py"
    assert scope.namespace("helper") is None
    assert scope.named("a") is not None and scope.named("c") is None
    assert scope.wildcard_modules == ("pkg/open.py",)
    assert scope.named("sep") is None
    # A function-local import is a binding of the file, as the analyzer's own maps read it.
    local = scope.named("f")
    assert local is not None and local.module == "pkg/sub.py"
    rel = result.import_scopes[str(tmp_path / "pkg/rel.py")]
    assert rel.namespace("sub") == "pkg/sub.py"
    assert rel.named("Thing") is not None
    # Every file has a table, an empty one included.
    assert set(result.import_scopes) == {str(tmp_path / p) for p in _TABLE_TREE}


def test_the_table_counts_what_it_leaves_out(tmp_path: Path) -> None:
    files = {
        **_TABLE_TREE,
        "a/src/dup/util.py": "", "b/src/dup/util.py": "",
        "tests/helpers.py": "", "helpers.py": "",
        "tests/test_x.py": "import dup.util\nimport helpers\nfrom . import x\n",
    }
    _write(tmp_path, files)
    roots = _detect_source_roots(tmp_path)
    analyses = {}
    for rel in files:
        analysis, _ = _extract_file_analysis(tmp_path / rel, tmp_path, roots)
        assert analysis is not None
        analyses[tmp_path / rel] = analysis
    modules = _ModuleFiles(analyses, tmp_path, roots, {})
    counts: Counter[str] = Counter()
    for rel in ("main.py", "pkg/rel.py", "tests/test_x.py"):
        _import_scope(tmp_path / rel, analyses[tmp_path / rel], modules, counts)
    assert counts["tie"] >= 1
    assert counts["sys_dir_and_root"] >= 1
    assert counts["relative_unresolved"] == 1  # ``from .. import beyond`` above ``pkg``
    assert counts["library"] >= 4
    assert counts["recorded"] >= 10


# --- the calls the analyzer binds itself -----------------------------------------


def test_an_import_binds_the_file_it_names_and_no_suffix_namesake(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/json.py": "def dumps(x): pass\n",
        "backend/app/__init__.py": "",
        "backend/app/crud.py": "def create(): pass\n",
        "main.py": (
            "from json import dumps\nfrom app.crud import create\nimport json\n"
            "def run():\n    dumps(1)\n    create()\n    json.loads('')\n"
        ),
    })
    edges = _edges(result, "main.py")
    assert not [e for e in edges if e.is_resolved]
    assert _stub(edges, "dumps").dst == "python:json:0-0:dumps:unresolved"
    assert _stub(edges, "create").dst == "python:app.crud:0-0:create:unresolved"
    assert _facts(_stub(edges, "loads")) == {"call_construct": "method", "qualifier": "json"}


def test_a_name_an_init_passes_on_binds_where_it_is_declared(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {
        "pkg/__init__.py": "from .impl import make\n",
        "pkg/impl.py": "def make(): pass\n",
        "other/impl.py": "def make(): pass\n",
        "main.py": "from pkg import make\ndef run():\n    make()\n",
    })
    (target,) = _resolved_to(_edges(result, "main.py"), 3)
    assert "pkg/impl.py" in target


# --- the stubs and their facts ------------------------------------------------------


def test_every_stub_names_its_callee_and_says_what_the_site_writes(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {
        "lib/__init__.py": "",
        "lib/mod.py": "class Base:\n    def __init__(self): pass\n    def go(self): pass\n",
        "main.py": (
            "import os\nimport os.path\nimport lib.mod\nfrom lib import mod\nfrom os import environ\n"
            "from lib.mod import Base\n"
            "class C(Base):\n"
            "    def __init__(self):\n"
            "        super().__init__()\n"
            "        super(C, self).go()\n"
            "        self.inherited()\n"
            "        self.conn.send()\n"
            "    @staticmethod\n"
            "    def s():\n"
            "        super().go()\n"
            "def run(b: Base, x):\n"
            "    os.getcwd()\n"
            "    os.Thing()\n"
            "    os.path.join('a')\n"
            "    mod.absent()\n"
            "    environ.get('k')\n"
            "    b.missing()\n"
            "    x.anything()\n"
            "    len(x)\n"
            "    unknown_name()\n"
            "    Base.absent_cls()\n"
            "    super(Other, x).go()\n"
            "    lib.mod.nothing()\n"
        ),
    })
    edges = _edges(result, "main.py")
    init = _stub(edges, "__init__", 9)
    assert _facts(init)["enclosing_class"] == "C" and _facts(init)["super_receiver"] is True
    assert _facts(init)["enclosing_class_id"].endswith(":C:class")
    assert init.evidence_type == "ast_call_direct"
    assert _facts(_stub(edges, "go", 10))["super_receiver"] is True
    assert _facts(_stub(edges, "inherited"))["enclosing_class"] == "C"
    assert _facts(_stub(edges, "send"))["inherited_field_receiver"] == "conn"
    assert "super_receiver" not in _facts(_stub(edges, "go", 15))
    assert "super_receiver" not in _facts(_stub(edges, "go", 27))
    assert _facts(_stub(edges, "getcwd")) == {"call_construct": "method", "qualifier": "os"}
    ctor = _stub(edges, "Thing")
    assert ctor.edge_type == "instantiates" and _facts(ctor) == {"qualifier": "os"}
    assert _facts(_stub(edges, "join"))["qualifier"] == "os.path"
    assert _stub(edges, "join").dst == "python:os.path:0-0:join:unresolved"
    assert _facts(_stub(edges, "absent"))["qualifier"] == "mod"
    assert _facts(_stub(edges, "get"))["qualifier"] == "environ"
    assert _facts(_stub(edges, "missing"))["receiver_type_hint"] == "Base"
    assert _facts(_stub(edges, "anything")) == {"call_construct": "method"}
    assert _stub(edges, "len").dst == "python:builtins:0-0:len:unresolved"
    unknown = _stub(edges, "unknown_name")
    assert unknown.dst_ref is not None and unknown.dst_ref.module_path == "external"
    assert _facts(_stub(edges, "absent_cls"))["qualifier"] == "Base"
    assert _facts(_stub(edges, "nothing"))["qualifier"] == "lib.mod"


# --- base classes ----------------------------------------------------------------------


def _extends(result, child: str) -> list[str]:
    return [
        e.dst for e in result.edges
        if e.edge_type == "extends" and e.src.endswith(f":{child}:class")
    ]


def test_a_base_is_the_class_python_binds_never_a_namesake(tmp_path: Path) -> None:
    result = _analyze(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/config.py": "class Config: pass\nclass Shared: pass\n",
        "pkg/star.py": "class Starred: pass\n",
        "pkg/star2.py": "class Twice: pass\n",
        "pkg/star3.py": "class Twice: pass\n",
        "other/config.py": "class Lonely: pass\nclass Shared: pass\n",
        "tests/conftest.py": (
            "from pkg.config import Config as BaseConfig\nfrom pkg.star import *\n"
            "from pkg.star2 import *\nfrom pkg.star3 import *\nfrom pkg.config import Missing\n"
            "class Config(BaseConfig): pass\n"
            "class A(Lonely): pass\n"
            "class B(Starred): pass\n"
            "class D(Twice): pass\n"
            "class E(Missing): pass\n"
            "class Local: pass\nclass F(Local): pass\n"
        ),
    })
    assert [d.split(":")[1] for d in _extends(result, "Config")] == [str(tmp_path / "pkg/config.py")]
    # One class of that name in the repository is not the base of a file that does not bind it.
    assert _extends(result, "A") == ["python:external:0-0:Lonely:unresolved"]
    assert [d.split(":")[1] for d in _extends(result, "B")] == [str(tmp_path / "pkg/star.py")]
    assert _extends(result, "D") == ["python:external:0-0:Twice:unresolved"]
    assert _extends(result, "E") == []
    assert [d.split(":")[-2] for d in _extends(result, "F")] == ["Local"]


# --- end to end, with a namesake beside each target -------------------------------------


def _map(root: Path, files: dict[str, str]) -> tuple[dict[str, dict], list[dict]]:
    _write(root / "repo", files)
    out = root / "out.json"
    run_behavior_map(repo_root=root / "repo", out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    return {n["id"]: n for n in data["nodes"]}, data["edges"]


def _resolved(nodes: dict[str, dict], edges: list[dict], line: int, path: str = "main.py") -> set[tuple]:
    """(target's path, target's name, mark) of each resolved call of ``path`` on ``line``."""
    return {
        (nodes[e["dst"]]["path"], nodes[e["dst"]]["name"], (e.get("meta") or {}).get("resolved_by"))
        for e in edges
        if e["type"] in ("calls", "instantiates") and e.get("is_resolved")
        and nodes.get(e["src"], {}).get("path") == path and e["line"] == line
    }


_NAMESAKES = {
    "pkg/__init__.py": "from .impl import Conn\nfrom .impl import *\n",
    "pkg/impl.py": (
        "__all__ = ['get', 'Conn']\n"
        "class Base:\n    def query(self): pass\n    def __init__(self): pass\n"
        "class Conn(Base):\n    def __init__(self):\n        super().__init__()\n"
        "def get(): pass\n"
    ),
    "other/__init__.py": "",
    "other/impl.py": (
        "class Conn:\n    def query(self): pass\n    def __init__(self): pass\n"
        "def get(): pass\ndef helper(): pass\ndef dumps(x): pass\n"
    ),
    "other/json.py": "def dumps(x): pass\n",
}


def test_calls_reach_what_the_file_binds_and_not_a_namesake(tmp_path: Path) -> None:
    nodes, edges = _map(tmp_path, {
        **_NAMESAKES,
        "main.py": (
            "import pkg\nimport json\nfrom pkg import Conn\n"
            "def helper(): pass\n"
            "def run(c: Conn, x):\n"
            "    pkg.get()\n"            # 6: through the package's star re-export
            "    c.query()\n"            # 7: typed receiver, inherited from Base
            "    helper()\n"             # 8: the caller's own module
            "    x.query()\n"            # 9: untyped receiver
            "    json.dumps(x)\n"        # 10: a library call the tree also declares
            "    Conn()\n"               # 11: a construction through the import
        ),
    })
    impl = "pkg/impl.py"
    assert _resolved(nodes, edges, 6) == {(impl, "get", "import")}
    assert _resolved(nodes, edges, 7) == {(impl, "Base.query", "receiver_type")}
    assert _resolved(nodes, edges, 8) == {("main.py", "helper", "same_file")}
    assert _resolved(nodes, edges, 9) == set()
    assert _resolved(nodes, edges, 10) == set()
    assert _resolved(nodes, edges, 11) == {(impl, "Conn", "import")}
    # super().__init__() reaches the parent's, not another class's __init__.
    assert _resolved(nodes, edges, 7, impl) == {(impl, "Base.__init__", "own_type")}


def test_a_later_library_import_of_a_name_leaves_it_out_of_the_table(tmp_path: Path) -> None:
    """The table follows the analyzer's own maps: a function-local library
    import made after a module-level in-tree import of the same name is the
    binding the analyzer's stub reports, and the linker must not bind that
    stub to the in-tree function."""
    nodes, edges = _map(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/taxonomy.py": "def get_language(p): pass\n",
        "main.py": (
            "from pkg.taxonomy import get_language\n"
            "def parser(name):\n"
            "    from tree_sitter_language_pack import get_language\n"
            "    return get_language(name)\n"
        ),
    })
    assert _resolved(nodes, edges, 4) == set()


def test_pytests_configured_pythonpath_is_on_a_test_files_path(tmp_path: Path) -> None:
    modules = _modules(tmp_path, {
        "pkg/tests/__init__.py": "",
        "pkg/tests/helpers.py": "def h(): pass\n",
        "pkg/tests/test_a.py": "from helpers import h\n",
        "pkg/tests/sub/__init__.py": "",
        "pkg/tests/sub/test_b.py": "",
        "pkg/lib.py": "",
        "recorded/shapes/__init__.py": "",
    })
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = "recorded"\n'
    )
    (tmp_path / "pkg" / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath = ["tests", 3]\n'
    )
    test_file = tmp_path / "pkg/tests/sub/test_b.py"
    assert modules.file_for("helpers", test_file) == tmp_path / "pkg/tests/helpers.py"
    assert modules.file_for("shapes", test_file) == tmp_path / "recorded/shapes/__init__.py"
    # The settings put a directory on a TEST run's path, not on a library module's.
    assert modules.file_for("helpers", tmp_path / "pkg/lib.py") is None
