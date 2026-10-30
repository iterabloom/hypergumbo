# SPDX-License-Identifier: AGPL-3.0-or-later
"""Node's entry rule for a package directory (js_package_entry.package_entry).

Core-package tests: CI tests each package in isolation, and the lang-mainstream
survey tests that reach these branches do not count toward core's coverage.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.js_package_entry import package_entry


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
