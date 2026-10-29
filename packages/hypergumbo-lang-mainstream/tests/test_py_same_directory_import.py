# SPDX-License-Identifier: AGPL-3.0-or-later
"""A bare import of a module beside the importing file resolves in-tree (INV-tugim).

Python puts a script's own directory first on ``sys.path``, and pytest's
default ``prepend`` import mode inserts a test file's basedir -- the first
directory upward that is not a package. So ``import conftest`` in
``tests/test_a.py`` and ``import util`` in ``scripts/run.py`` name first-party
files. The import resolver only looked a bare name up from the source root
(``conftest``, ``util``), missed, and minted an ``external_symbol`` the
dependency linker then counted as a third-party import (the INV-nuzas family).

The directory that is searched is the nearest one that is NOT a package: inside
a package Python 3 has no implicit relative import, so ``import b`` in
``pkg/a.py`` is not ``pkg/b.py`` and must stay external.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core.cli import run_behavior_map


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def _survey(root: Path) -> dict:
    out = root.parent / "out.json"
    run_behavior_map(repo_root=root, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _import_dst(data: dict, src_suffix: str, name: str) -> str:
    """The dst of the one import edge from ``src_suffix`` naming ``name``."""
    edges = [
        e for e in data["edges"]
        if e["type"] == "imports" and e["src"].endswith(src_suffix + ":1-1:file:file")
        and name in e["dst"]
    ]
    assert len(edges) == 1, f"expected one import of {name} from {src_suffix}: {edges}"
    return edges[0]["dst"]


def _external_ids(data: dict) -> list[str]:
    return [n["id"] for n in data["nodes"] if n["kind"] == "external_symbol"]


def test_a_test_file_importing_its_conftest_reaches_it(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root, {
        "tests/conftest.py": "def helper():\n    return 2\n",
        "tests/test_a.py": "import conftest\n\ndef test_a():\n    assert conftest.helper() == 2\n",
    })
    data = _survey(root)
    assert _import_dst(data, "tests/test_a.py", "conftest") == (
        "python:tests/conftest.py:1-1:file:file"
    )
    assert not any("conftest" in i for i in _external_ids(data)), _external_ids(data)


def test_a_script_importing_its_sibling_reaches_it(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root, {
        "scripts/util.py": "def util():\n    return 3\n",
        "scripts/run.py": (
            "import util\n\ndef main():\n    return util.util()\n\n"
            "if __name__ == \"__main__\":\n    main()\n"
        ),
    })
    data = _survey(root)
    assert _import_dst(data, "scripts/run.py", "util") == "python:scripts/util.py:1-1:file:file"


def test_a_from_import_of_a_sibling_reaches_it(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root, {
        "scripts/util.py": "def util():\n    return 3\n",
        "scripts/run.py": (
            "from util import util\n\ndef main():\n    return util()\n\n"
            "if __name__ == \"__main__\":\n    main()\n"
        ),
    })
    data = _survey(root)
    assert _import_dst(data, "scripts/run.py", "util") == "python:scripts/util.py:1-2:util:function"
    assert not any(":util:" in i for i in _external_ids(data)), _external_ids(data)


def test_the_basedir_is_the_nearest_directory_that_is_not_a_package(tmp_path: Path) -> None:
    """``tests/unit/`` is a package under a non-package ``tests/``: pytest's
    basedir for ``tests/unit/test_x.py`` is ``tests/``, so ``import helper``
    names ``tests/helper.py``."""
    root = tmp_path / "repo"
    _write(root, {
        "tests/helper.py": "def h():\n    return 1\n",
        "tests/unit/__init__.py": "",
        "tests/unit/test_x.py": "import helper\n",
    })
    data = _survey(root)
    assert _import_dst(data, "tests/unit/test_x.py", "helper") == (
        "python:tests/helper.py:1-1:file:file"
    )


def test_inside_a_package_a_bare_import_is_not_a_sibling(tmp_path: Path) -> None:
    """Python 3 has no implicit relative import: ``import b`` in ``pkg/a.py``
    is a top-level ``b``, not ``pkg/b.py``."""
    root = tmp_path / "repo"
    _write(root, {
        "pkg/__init__.py": "",
        "pkg/a.py": "import b\n",
        "pkg/b.py": "def f():\n    return 1\n",
    })
    data = _survey(root)
    assert _import_dst(data, "pkg/a.py", "b") == "python:b:0-0:b:external_symbol"


def test_a_third_party_import_beside_a_sibling_stays_external(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root, {
        "scripts/util.py": "def util():\n    return 3\n",
        "scripts/run.py": "import requests\nimport util\n\nif __name__ == \"__main__\":\n    pass\n",
    })
    data = _survey(root)
    assert _import_dst(data, "scripts/run.py", "requests") == (
        "python:requests:0-0:requests:external_symbol"
    )
    assert _import_dst(data, "scripts/run.py", "util") == "python:scripts/util.py:1-1:file:file"


def test_a_namespace_package_module_does_not_shadow_the_stdlib(tmp_path: Path) -> None:
    """Corpus A/B (poetry): ``src/poetry/`` has no ``__init__.py`` -- a PEP 420
    namespace package -- and holds a ``json`` subpackage. ``import json`` in
    ``src/poetry/utils/env.py`` is the stdlib, because a library module is
    imported as ``poetry.utils.env`` and its directory is never on
    ``sys.path``. Only a test-tree file or a script run directly gets one."""
    root = tmp_path / "repo"
    _write(root, {
        "pyproject.toml": '[project]\nname = "poetry"\nversion = "0"\n',
        "src/poetry/json/__init__.py": "def loads(s):\n    return s\n",
        "src/poetry/utils/__init__.py": "",
        "src/poetry/utils/env.py": "import json\n",
    })
    data = _survey(root)
    assert _import_dst(data, "src/poetry/utils/env.py", "json") == (
        "python:json:0-0:json:external_symbol"
    )


def test_a_script_is_its_own_directory_but_a_library_module_is_not(tmp_path: Path) -> None:
    """Both files sit in the non-package ``tools/`` beside ``util.py``; only the
    one run directly (a main guard) has ``tools/`` on ``sys.path``."""
    root = tmp_path / "repo"
    _write(root, {
        "tools/util.py": "def util():\n    return 3\n",
        "tools/cli.py": "import util\n\nif __name__ == '__main__':\n    util.util()\n",
        "tools/lib.py": "import util\n",
    })
    data = _survey(root)
    assert _import_dst(data, "tools/cli.py", "util") == "python:tools/util.py:1-1:file:file"
    assert _import_dst(data, "tools/lib.py", "util") == "python:util:0-0:util:external_symbol"


def test_a_from_import_of_submodules_beside_the_importer_reaches_each(tmp_path: Path) -> None:
    """Corpus A/B (vmaf): ``from helpers import a, b`` names two SUBMODULES;
    each resolves to its own file, as the source-root arm already does, not
    both to ``helpers/__init__.py``."""
    root = tmp_path / "repo"
    _write(root, {
        "tests/helpers/__init__.py": "",
        "tests/helpers/a.py": "def fa():\n    return 1\n",
        "tests/helpers/b.py": "def fb():\n    return 2\n",
        "tests/test_x.py": "from helpers import a, b\n",
    })
    data = _survey(root)
    dsts = sorted(
        e["dst"] for e in data["edges"]
        if e["type"] == "imports" and e["src"].endswith("tests/test_x.py:1-1:file:file")
    )
    assert dsts == [
        "python:tests/helpers/a.py:1-1:file:file",
        "python:tests/helpers/b.py:1-1:file:file",
    ], dsts
