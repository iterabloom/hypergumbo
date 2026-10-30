# SPDX-License-Identifier: AGPL-3.0-or-later
"""The file a bare npm specifier names inside an in-tree package: Node's entry rule.

ONE COPY, TWO CONSUMERS. The js_module linker resolves a workspace sibling's
import to its entry file (WI-rilof); the JS/TS analyzer resolves a DEFAULT
import of that sibling to the entry's ``export default`` declaration (WI-halol).
Both must agree on which file a bare ``require('@ws/c')`` loads, so the rule
lives here rather than in either: ``exports`` / ``exports["."]`` (its
``import`` / ``require`` / ``default`` condition), then ``module``, then
``main``, then ``index.<ext>``, then the package's own ``src/index.<ext>``
when the declared entry is build output not in the tree.

Reads go through ``linkers._text_filters.read_source_text``, which records the
read against the active linker's read log when one is bound and is silent
otherwise -- so the linker's ``files_analyzed`` still counts its
``package.json`` reads and an analyzer call counts nothing.
"""
from __future__ import annotations

import json as json_module
from pathlib import Path

from .linkers._text_filters import read_source_text

# Extensions to probe when import path has no extension, in priority order.
# .js first because it's the most common in real-world JS/TS projects.
PROBE_EXTENSIONS = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".mts", ".vue", ".json")


def probe_file(base_path: Path) -> Path | None:
    """Probe for a file with various extensions and index patterns.

    Given a base path (without extension), tries to find the actual file:
    1. If the path already has an extension and exists, return it.
    2. Try appending each extension in PROBE_EXTENSIONS.
    3. If it's a directory, try {dir}/index.{ext}.

    Args:
        base_path: The base file path (possibly without extension).

    Returns:
        Resolved Path if found, None otherwise.
    """
    # If the path already exists as a file, return it
    if base_path.is_file():
        return base_path

    # Try appending extensions
    for ext in PROBE_EXTENSIONS:
        candidate = base_path.with_suffix(ext)
        if candidate.is_file():
            return candidate

    # If it's a directory, try index files
    if base_path.is_dir():
        for ext in PROBE_EXTENSIONS:
            index = base_path / f"index{ext}"
            if index.is_file():
                return index

    return None


def package_entry(root: Path) -> Path | None:
    """A workspace package's entry file, the way Node finds it for a bare
    ``require``: a string ``exports`` or ``exports["."]`` (its ``import`` /
    ``require`` / ``default`` condition when it is a map), then ``module``,
    then ``main``, then ``index.<ext>``.

    A TypeScript package's entry usually names BUILD output
    (``"main": "dist/cjs/index.js"``) that is not in the tree, so when no
    declared entry exists the package's own source entry, ``src/index.<ext>``,
    answers instead (apollo-server: four of its workspace packages)."""
    # A workspace root's package.json was read as a JSON object naming a
    # package (the js_module workspace index; js_ts's _package_json_name),
    # but a relative DIRECTORY import (``require('..')``, WI-pulid) reaches
    # here with a manifest nobody has validated: an unreadable or non-object
    # one declares no entry, and the index probe below answers.
    try:
        data = json_module.loads(read_source_text(root / "package.json", errors="ignore"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    exports = data.get("exports")
    if isinstance(exports, dict):
        exports = exports.get(".", exports)
        if isinstance(exports, dict):
            exports = next(
                (exports[k] for k in ("import", "require", "default")
                 if isinstance(exports.get(k), str)),
                None,
            )
    for field_value in (exports, data.get("module"), data.get("main")):
        if isinstance(field_value, str):
            found = probe_file((root / field_value).resolve())
            if found is not None:
                return found
    return probe_file(root / "index") or probe_file(root / "src" / "index")
