# SPDX-License-Identifier: AGPL-3.0-or-later
"""A call spelled through a fully dotted module path binds in the tree (WI-lovun).

``import pkg.sub.deep`` binds only ``pkg``; the call site then spells the whole
path, ``pkg.sub.deep.deep_fn()``. The analyzer resolved ``deep.deep_fn()``
after ``from pkg.sub import deep`` (Case 2e) but sent the dotted spelling to the
WI-zigah emitter, which is for modules that are NOT in the tree: the call
landed on ``python:pkg.sub.deep:0-0:deep_fn:external_symbol`` and the real
``deep_fn`` read as uncalled. In a workspace the phantom also carried a
sibling package's prefix, the shape INV-nuzas's acceptance property forbids.

A dotted call on an in-tree module CONSTANT (``pkg.REGISTRY.get()``) takes the
answer the from-import spelling already has (WI-hotug CASE B): a
``references`` edge to the variable, and no stub naming a module that does not
exist.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/py.py
from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _survey(repo: Path, files: dict[str, str]) -> dict:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    out = repo / "out.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _calls_from(data: dict, caller: str) -> list[dict]:
    src = next(n["id"] for n in data["nodes"] if n["name"] == caller)
    return [e for e in data["edges"] if e["type"] == "calls" and e["src"] == src]


def _external_ids(data: dict) -> list[str]:
    return [n["id"] for n in data["nodes"] if n["kind"] == "external_symbol"]


_PKG = {
    "mypkg/__init__.py": "REGISTRY = {}\n",
    "mypkg/core.py": "CONSTANTS = {}\ndef core_fn():\n    return 1\n",
    "mypkg/sub/__init__.py": "",
    "mypkg/sub/deep.py": "def deep_fn():\n    return 1\nclass Deep:\n    pass\n",
}


def test_dotted_submodule_call_binds_the_in_tree_function(tmp_path: Path) -> None:
    data = _survey(tmp_path, {**_PKG, "app.py": (
        "import mypkg.sub.deep\n"
        "import mypkg.core\n"
        "def run():\n"
        "    mypkg.core.core_fn()\n"
        "    return mypkg.sub.deep.deep_fn()\n"
    )})
    dsts = {e["dst"] for e in _calls_from(data, "run")}
    assert "python:mypkg/sub/deep.py:1-2:deep_fn:function" in dsts, dsts
    assert "python:mypkg/core.py:2-3:core_fn:function" in dsts, dsts
    assert not [i for i in _external_ids(data) if "mypkg" in i], _external_ids(data)


def test_dotted_class_construction_instantiates_the_in_tree_class(
    tmp_path: Path,
) -> None:
    data = _survey(tmp_path, {**_PKG, "app.py": (
        "import mypkg.sub.deep\n"
        "def run():\n"
        "    return mypkg.sub.deep.Deep()\n"
    )})
    run_id = next(n["id"] for n in data["nodes"] if n["name"] == "run")
    made = {
        e["dst"] for e in data["edges"]
        if e["type"] == "instantiates" and e["src"] == run_id
    }
    assert made == {"python:mypkg/sub/deep.py:3-4:Deep:class"}, made


def test_from_import_spelling_still_binds(tmp_path: Path) -> None:
    """Control: the spelling that already worked keeps working."""
    data = _survey(tmp_path, {**_PKG, "app.py": (
        "from mypkg.sub import deep\n"
        "def run():\n"
        "    return deep.deep_fn()\n"
    )})
    dsts = {e["dst"] for e in _calls_from(data, "run")}
    assert dsts == {"python:mypkg/sub/deep.py:1-2:deep_fn:function"}


def test_aliased_dotted_import_binds_through_the_alias(tmp_path: Path) -> None:
    data = _survey(tmp_path, {**_PKG, "app.py": (
        "import mypkg.sub as s\n"
        "import mypkg.sub.deep\n"
        "def run():\n"
        "    return s.deep.deep_fn()\n"
    )})
    dsts = {e["dst"] for e in _calls_from(data, "run")}
    assert dsts == {"python:mypkg/sub/deep.py:1-2:deep_fn:function"}


def test_dotted_call_on_an_in_tree_constant_references_it(tmp_path: Path) -> None:
    data = _survey(tmp_path, {**_PKG, "app.py": (
        "import mypkg.core\n"
        "def run():\n"
        "    mypkg.REGISTRY.get(1)\n"
        "    mypkg.core.CONSTANTS.values()\n"
    )})
    run_id = next(n["id"] for n in data["nodes"] if n["name"] == "run")
    refs = {
        e["dst"] for e in data["edges"]
        if e["type"] == "references" and e["src"] == run_id
    }
    assert "python:mypkg/__init__.py:1-1:REGISTRY:variable" in refs, refs
    assert "python:mypkg/core.py:1-1:CONSTANTS:variable" in refs, refs
    assert not [i for i in _external_ids(data) if "mypkg" in i], _external_ids(data)


def test_stdlib_dotted_call_keeps_its_module_path(tmp_path: Path) -> None:
    """Control: a dotted module that is not in the tree keeps the WI-zigah
    module-qualified stub io-boundaries and taint match on."""
    data = _survey(tmp_path, {"app.py": (
        "import urllib.request\n"
        "def run(u):\n"
        "    return urllib.request.urlopen(u)\n"
    )})
    dsts = {e["dst"] for e in _calls_from(data, "run")}
    assert any(d.startswith("python:urllib.request:0-0:urlopen:") for d in dsts), dsts


def test_parameter_shadowing_the_root_does_not_bind(tmp_path: Path) -> None:
    """Control: a parameter named like the imported root is a value, not the
    module, so the call is not bound to the in-tree function."""
    data = _survey(tmp_path, {**_PKG, "app.py": (
        "import mypkg.sub.deep\n"
        "def run(mypkg):\n"
        "    return mypkg.sub.deep.deep_fn()\n"
    )})
    dsts = {e["dst"] for e in _calls_from(data, "run")}
    assert "python:mypkg/sub/deep.py:1-2:deep_fn:function" not in dsts, dsts


def test_workspace_sibling_dotted_call_binds_without_a_phantom(tmp_path: Path) -> None:
    """INV-nuzas acceptance property: no external_symbol carries a workspace
    package's prefix on a production call path."""
    data = _survey(tmp_path, {
        "pyproject.toml": '[tool.uv.workspace]\nmembers = ["packages/*"]\n',
        "packages/pkg-b/pyproject.toml": '[project]\nname = "pkg-b"\nversion = "0.1.0"\n',
        "packages/pkg-b/src/pkg_b/__init__.py": "REGISTRY = {}\n",
        "packages/pkg-b/src/pkg_b/sub/__init__.py": "",
        "packages/pkg-b/src/pkg_b/sub/deep.py": "def deep_fn():\n    return 1\n",
        "packages/pkg-a/pyproject.toml": (
            '[project]\nname = "pkg-a"\nversion = "0.1.0"\n'
            'dependencies = ["pkg-b"]\n'
        ),
        "packages/pkg-a/src/pkg_a/__init__.py": "",
        "packages/pkg-a/src/pkg_a/app.py": (
            "import pkg_b.sub.deep\n"
            "def run():\n"
            "    pkg_b.REGISTRY.get(1)\n"
            "    return pkg_b.sub.deep.deep_fn()\n"
        ),
    })
    dsts = {e["dst"] for e in _calls_from(data, "run")}
    assert (
        "python:packages/pkg-b/src/pkg_b/sub/deep.py:1-2:deep_fn:function" in dsts
    ), dsts
    assert not [i for i in _external_ids(data) if "pkg_b" in i], _external_ids(data)
