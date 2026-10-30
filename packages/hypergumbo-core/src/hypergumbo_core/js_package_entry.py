# SPDX-License-Identifier: AGPL-3.0-or-later
"""The file a bare npm specifier names inside an in-tree package: Node's entry rule.

It also holds Node's core-module list (``is_node_core_module``), which the
linker reads so it never mints an npm package for ``fs``, and js_ts reads so
an import of one is external.

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

# Node's core modules: an import of one is the runtime's own module, external
# with no manifest and never an npm package (WI-tufob). ONE copy, read by the
# js_module linker (no package node) and js_ts (an import-bound external).
NODE_CORE_MODULES = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console",
    "constants", "crypto", "dgram", "diagnostics_channel", "dns", "domain",
    "events", "fs", "http", "http2", "https", "inspector", "module", "net",
    "os", "path", "perf_hooks", "process", "punycode", "querystring",
    "readline", "repl", "stream", "string_decoder", "timers", "tls",
    "trace_events", "tty", "url", "util", "v8", "vm", "wasi",
    "worker_threads", "zlib",
})


def is_node_core_module(specifier: str) -> bool:
    """``fs``, ``fs/promises``, ``node:anything`` -- not ``fs-extra``."""
    return specifier.startswith("node:") or specifier.split("/", 1)[0] in NODE_CORE_MODULES


# Extensions to probe when import path has no extension, in priority order.
# .js first because it's the most common in real-world JS/TS projects.
PROBE_EXTENSIONS = (".js", ".ts", ".jsx", ".tsx", ".mjs", ".mts", ".vue", ".json")


# TypeScript's ESM convention: a specifier spelled with the JS extension the
# build will produce (``./x.js``) names the TS source before the build.
_JS_TO_TS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}


def probe_file(base_path: Path) -> Path | None:
    """The file a relative specifier names, the way Node looks for it.

    1. The path itself, when it is a file.
    2. The path with each extension in PROBE_EXTENSIONS APPENDED. WI-dunab:
       this used ``Path.with_suffix``, which REPLACES the last dotted
       segment, so ``./X.test-wrapper`` became ``X.tsx`` -- an existing,
       different file (30 nuclear test imports bound to the component).
    3. For a specifier ending in a JS extension, its TS twin
       (``./x.js`` -> ``x.ts``; ``_JS_TO_TS``).
    4. A directory's ``index.<ext>``.

    Args:
        base_path: The base file path (possibly without extension).

    Returns:
        Resolved Path if found, None otherwise.
    """
    if base_path.is_file():
        return base_path
    for ext in PROBE_EXTENSIONS:
        candidate = base_path.with_name(base_path.name + ext)
        if candidate.is_file():
            return candidate
    for ext in _JS_TO_TS.get(base_path.suffix, ()):
        candidate = base_path.with_suffix(ext)
        if candidate.is_file():
            return candidate
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
