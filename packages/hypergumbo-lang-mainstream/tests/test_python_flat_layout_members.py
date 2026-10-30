# SPDX-License-Identifier: AGPL-3.0-or-later
"""A flat-layout workspace member is a Python source root (WI-homih, INV-nuzas).

A member that keeps its package beside its manifest
(``packages/pkg-c/pyproject.toml`` + ``packages/pkg-c/pkg_c/__init__.py``, no
``src/``) is installed with ``packages/pkg-c`` on ``sys.path``, so its files are
``pkg_c`` and ``pkg_c.tools``. ``_detect_source_roots`` collected only
directories named ``src``, so the member's files were named by their repo path
(``packages.pkg-c.pkg_c.tools``), nothing in the tree was named ``pkg_c``, and
every ``import pkg_c`` / ``pkg_c.tools.shout()`` landed on an external stand-in
although the code is right there.

A flat member's root covers only the PACKAGES it would install -- the child
directories setuptools' flat-layout discovery would pick (an ``__init__.py``,
not ``tests`` / ``docs`` / ``examples`` / ...). Its ``tests/`` and loose files
keep their repo-path names, because two members' ``tests/test_api.py`` would
otherwise both be ``tests.test_api``. For the same reason a top-level package
name two flat members both supply roots neither: a module name must name one
file.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_lang_mainstream.py import _detect_source_roots, _module_name_from_path


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def _survey(root: Path) -> dict:
    out = root.parent / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


_MEMBER = {
    "packages/pkg-c/pyproject.toml": '[project]\nname = "pkg-c"\nversion = "0.1.0"\n',
    "packages/pkg-c/pkg_c/__init__.py": '"""pkg_c."""\nfrom pkg_c import tools\n',
    "packages/pkg-c/pkg_c/tools.py": (
        'def shout(s):\n    return s.upper()\n\n\n'
        'class Loud:\n    def go(self):\n        return shout("go")\n'
    ),
}

_WORKSPACE = {
    "pyproject.toml": (
        '[project]\nname = "ws-root"\nversion = "0.1.0"\n\n'
        '[tool.uv.workspace]\nmembers = ["packages/*"]\n'
    ),
    "packages/pkg-a/pyproject.toml": (
        '[project]\nname = "pkg-a"\nversion = "0.1.0"\ndependencies = ["pkg-c"]\n'
    ),
    "packages/pkg-a/src/pkg_a/__init__.py": "",
    "packages/pkg-a/src/pkg_a/app.py": (
        "import pkg_c\n\n\n"
        "def more():\n"
        '    return pkg_c.tools.shout("y"), pkg_c.tools.Loud().go()\n'
    ),
    **_MEMBER,
}


class TestTheRoots:
    def test_a_flat_member_is_a_root(self, tmp_path: Path) -> None:
        _write(tmp_path, _WORKSPACE)
        roots = _detect_source_roots(tmp_path)
        assert tmp_path / "packages" / "pkg-c" in roots
        assert tmp_path / "packages" / "pkg-a" / "src" in roots

    def test_a_src_layout_member_s_own_directory_is_not(self, tmp_path: Path) -> None:
        """pkg-a has a manifest and src/: src is its root, not the member dir,
        even with a package beside src."""
        _write(tmp_path, {**_WORKSPACE, "packages/pkg-a/stray/__init__.py": ""})
        assert tmp_path / "packages" / "pkg-a" not in _detect_source_roots(tmp_path)

    def test_the_repo_root_is_not_listed(self, tmp_path: Path) -> None:
        """A flat package at the repo root is already named from the root."""
        _write(tmp_path, {"pyproject.toml": "[project]\nname = 'x'\n", "x/__init__.py": ""})
        assert _detect_source_roots(tmp_path) == []

    def test_setup_py_and_setup_cfg_are_manifests(self, tmp_path: Path) -> None:
        _write(tmp_path, {
            "libs/one/setup.py": "", "libs/one/one/__init__.py": "",
            "libs/two/setup.cfg": "", "libs/two/two/__init__.py": "",
            "libs/three/three/__init__.py": "",
        })
        roots = _detect_source_roots(tmp_path)
        assert roots == [tmp_path / "libs" / "one", tmp_path / "libs" / "two"]

    def test_a_member_whose_only_packages_are_not_installed_is_not_a_root(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, {
            "libs/one/pyproject.toml": "", "libs/one/tests/__init__.py": "",
            "libs/one/docs/__init__.py": "", "libs/one/_private/__init__.py": "",
        })
        assert _detect_source_roots(tmp_path) == []

    def test_a_package_name_two_members_supply_roots_neither(self, tmp_path: Path) -> None:
        """poetry's tests/fixtures hold many projects each with simple_project/."""
        _write(tmp_path, {
            "fixtures/a/pyproject.toml": "", "fixtures/a/simple_project/__init__.py": "",
            "fixtures/b/pyproject.toml": "", "fixtures/b/simple_project/__init__.py": "",
            "fixtures/c/pyproject.toml": "", "fixtures/c/unique_project/__init__.py": "",
        })
        assert _detect_source_roots(tmp_path) == [tmp_path / "fixtures" / "c"]


class TestTheModuleNames:
    def test_a_member_package_file_is_named_from_the_member(self, tmp_path: Path) -> None:
        _write(tmp_path, _WORKSPACE)
        roots = _detect_source_roots(tmp_path)
        tools = tmp_path / "packages" / "pkg-c" / "pkg_c" / "tools.py"
        assert _module_name_from_path(tools, tmp_path, roots) == "pkg_c.tools"
        init = tmp_path / "packages" / "pkg-c" / "pkg_c" / "__init__.py"
        assert _module_name_from_path(init, tmp_path, roots) == "pkg_c.__init__"

    def test_a_member_s_tests_and_loose_files_keep_their_repo_names(
        self, tmp_path: Path,
    ) -> None:
        _write(tmp_path, {
            **_WORKSPACE,
            "packages/pkg-c/tests/__init__.py": "",
            "packages/pkg-c/tests/test_tools.py": "def test_t():\n    pass\n",
            "packages/pkg-c/conftest.py": "",
        })
        roots = _detect_source_roots(tmp_path)
        member = tmp_path / "packages" / "pkg-c"
        assert _module_name_from_path(member / "tests" / "test_tools.py", tmp_path, roots) == (
            "packages.pkg-c.tests.test_tools"
        )
        assert _module_name_from_path(member / "conftest.py", tmp_path, roots) == (
            "packages.pkg-c.conftest"
        )


def _external_ids(data: dict) -> list[str]:
    return [n["id"] for n in data["nodes"] if n["kind"] == "external_symbol"]


def _edges_from(data: dict, path: str, edge_type: str) -> list[str]:
    return sorted(
        e["dst"] for e in data["edges"]
        if e["type"] == edge_type and f":{path}:" in e["src"]
    )


class TestThroughTheSurvey:
    """The filed repro (INV-nuzas re-check fixture ``py``), production path."""

    def test_every_filed_shape_lands_on_the_member_s_code(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _write(root, _WORKSPACE)
        data = _survey(root)
        assert not any("pkg_c" in i for i in _external_ids(data)), _external_ids(data)
        app = "packages/pkg-a/src/pkg_a/app.py"
        assert "python:packages/pkg-c/pkg_c/__init__.py:1-1:file:file" in _edges_from(
            data, app, "imports",
        )
        calls = _edges_from(data, app, "calls")
        assert any(d.startswith("python:packages/pkg-c/pkg_c/tools.py:") and d.endswith(":shout:function")
                   for d in calls), calls
        assert any(d.startswith("python:packages/pkg-c/pkg_c/tools.py:") and d.endswith(":Loud.go:method")
                   for d in calls), calls
        init = "packages/pkg-c/pkg_c/__init__.py"
        assert "python:packages/pkg-c/pkg_c/tools.py:1-1:file:file" in _edges_from(
            data, init, "imports",
        )

    def test_no_import_of_the_member_lands_outside_the_tree(self, tmp_path: Path) -> None:
        """Reach control for the call assertions: the constructor and the
        method reached through the dotted chain are the member's own."""
        root = tmp_path / "repo"
        _write(root, _WORKSPACE)
        data = _survey(root)
        dsts = _edges_from(data, "packages/pkg-a/src/pkg_a/app.py", "instantiates")
        assert any(
            d.startswith("python:packages/pkg-c/pkg_c/tools.py:") and d.endswith(":Loud:class")
            for d in dsts
        ), dsts

    def test_the_root_level_flat_package_is_unchanged(self, tmp_path: Path) -> None:
        """Control: the same package at the repo root already resolved."""
        root = tmp_path / "repo"
        _write(root, {
            "pyproject.toml": '[project]\nname = "pkg-c"\nversion = "0.1.0"\n',
            "pkg_c/__init__.py": _MEMBER["packages/pkg-c/pkg_c/__init__.py"],
            "pkg_c/tools.py": _MEMBER["packages/pkg-c/pkg_c/tools.py"],
            "app/main.py": "import pkg_c\n\n\ndef run():\n    return pkg_c.tools.shout('y')\n",
        })
        data = _survey(root)
        assert not any("pkg_c" in i for i in _external_ids(data)), _external_ids(data)
        calls = _edges_from(data, "app/main.py", "calls")
        assert any(d.startswith("python:pkg_c/tools.py:") and d.endswith(":shout:function")
                   for d in calls), calls


_ROOT_PKG = {
    "pyproject.toml": '[project]\nname = "pkg-c"\nversion = "0.1.0"\n',
    "pkg_c/__init__.py": "",
    "pkg_c/tools.py": _MEMBER["packages/pkg-c/pkg_c/tools.py"],
}


class TestADottedConstructorPath:
    """``pkg_c.tools.Loud().go()`` minted ``python:pkg_c.tools.Loud:0-0:go`` in
    ANY layout: ``_resolve_call_target`` knew ``tools.Loud()`` but not the fully
    dotted spelling WI-lovun taught ``_process_call``, so the constructor
    resolver typed the result as an external ``pkg_c.tools.Loud``."""

    def test_a_method_on_the_constructed_object_is_the_class_s(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        _write(root, {**_ROOT_PKG, "app/main.py": (
            "import pkg_c.tools\n\n\n"
            "def inline():\n    return pkg_c.tools.Loud().go()\n\n\n"
            "def assigned():\n    x = pkg_c.tools.Loud()\n    return x.go()\n"
        )})
        data = _survey(root)
        assert not any("pkg_c" in i for i in _external_ids(data)), _external_ids(data)
        by_src: dict[str, list[str]] = {}
        for e in data["edges"]:
            if e["type"] == "calls" and "app/main.py" in e["src"]:
                by_src.setdefault(e["src"].split(":")[-2], []).append(e["dst"])
        for fn in ("inline", "assigned"):
            assert "python:pkg_c/tools.py:6-7:Loud.go:method" in by_src[fn], (fn, by_src)

    def test_a_stdlib_dotted_constructor_keeps_its_external_type(self, tmp_path: Path) -> None:
        """Control: nothing in the tree is ``http.client``."""
        root = tmp_path / "repo"
        _write(root, {"app/main.py": (
            "import http.client\n\n\n"
            "def fetch(h):\n    c = http.client.HTTPConnection(h)\n    c.request('GET', '/')\n"
        )})
        data = _survey(root)
        assert any(
            e["type"] == "calls" and e["dst"].startswith("python:http.client.HTTPConnection:")
            and e["dst"].endswith(":request:external_symbol")
            for e in data["edges"]
        ), [e["dst"] for e in data["edges"] if e["type"] == "calls"]
