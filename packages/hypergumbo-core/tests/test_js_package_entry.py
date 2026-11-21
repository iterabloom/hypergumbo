# SPDX-License-Identifier: AGPL-3.0-or-later
"""Node's entry rule for a package directory (js_package_entry.package_entry),
its package scope (``nearest_package_root``) and the ``exports`` declaration
(``declares_exports_map``).

Core-package tests: CI tests each package in isolation, and the lang-mainstream
survey tests that reach these branches do not count toward core's coverage.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.js_package_entry import (
    declares_exports_map, nearest_package_root, package_entry,
)


def test_main_names_the_entry(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"main": "lib/m.js"}')
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "m.js").write_text("")
    (tmp_path / "index.js").write_text("")
    assert package_entry(tmp_path) == tmp_path / "lib" / "m.js"


@pytest.mark.parametrize("manifest", ["{not json", "[1, 2]", '"a string"'])
def test_an_unreadable_or_non_object_manifest_declares_no_entry(
    tmp_path: Path, manifest: str,
) -> None:
    """A relative directory import (``require('..')``, WI-pulid) reaches here
    with a manifest nobody validated: it declares nothing, the index answers."""
    (tmp_path / "package.json").write_text(manifest)
    (tmp_path / "index.js").write_text("")
    assert package_entry(tmp_path) == tmp_path / "index.js"


def test_no_declared_entry_falls_back_to_src_index(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"main": "dist/cjs/index.js"}')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("")
    assert package_entry(tmp_path) == tmp_path / "src" / "index.ts"


def test_a_conditional_exports_map_names_its_import_condition(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"exports": {".": {"types": "./t.d.ts", "import": "./esm.js", "require": "./cjs.js"}}}'
    )
    for name in ("esm.js", "cjs.js", "index.js"):
        (tmp_path / name).write_text("")
    assert package_entry(tmp_path) == tmp_path / "esm.js"


def test_the_nearest_package_json_is_the_package(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "pkg" / "src").mkdir(parents=True)
    (tmp_path / "pkg" / "package.json").write_text("{}")
    source = tmp_path / "pkg" / "src" / "a.ts"
    source.write_text("")
    assert nearest_package_root(source) == tmp_path / "pkg"
    assert nearest_package_root(source.parent) == tmp_path / "pkg"
    assert nearest_package_root(tmp_path / "other") == tmp_path


def test_the_walk_stops_at_the_stop_directory(tmp_path: Path) -> None:
    """A manifest above the analysed tree is not the tree's."""
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "repo" / "src").mkdir(parents=True)
    assert nearest_package_root(tmp_path / "repo" / "src", stop=tmp_path / "repo") is None
    assert nearest_package_root(tmp_path / "repo" / "src") == tmp_path
    (tmp_path / "repo" / "package.json").write_text("{}")
    assert nearest_package_root(
        tmp_path / "repo" / "src", stop=tmp_path / "repo") == tmp_path / "repo"


def test_no_package_json_to_the_filesystem_root(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    found = nearest_package_root(tmp_path / "a")
    assert found is None or tmp_path not in (found, *found.parents)


@pytest.mark.parametrize(("manifest", "declared"), [
    ('{"exports": "./i.js"}', True),
    ('{"exports": {".": "./i.js", "./x": "./x.js"}}', True),
    ('{"exports": {"import": "./i.mjs", "require": "./i.cjs"}}', True),
    ('{"exports": {".": {"node": {"import": "./i.mjs"}}}}', True),
    ('{"exports": ["./i.js"]}', True),
    ('{"main": "./i.js", "module": "./m.js", "types": "./t.d.ts"}', False),
    ('{"exports": null}', False),
    ('{"exports": ""}', False),
    ('{"exports": {}}', False),
    ('{"exports": []}', False),
    ('{"exports": 1}', False),
    ('{"exports": "./i.js",}', False),
    ('["exports"]', False),
])
def test_declares_exports_map(tmp_path: Path, manifest: str, declared: bool) -> None:
    (tmp_path / "package.json").write_text(manifest)
    assert declares_exports_map(tmp_path) is declared


def test_no_manifest_declares_no_exports(tmp_path: Path) -> None:
    assert declares_exports_map(tmp_path) is False
