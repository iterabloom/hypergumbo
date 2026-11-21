# SPDX-License-Identifier: AGPL-3.0-or-later
"""An Electron preload script is found from what the code decides.

Electron loads a preload script before a window's page: the file a
``BrowserWindow``'s ``webPreferences.preload`` names, which exposes an API
to the page through ``contextBridge.exposeInMainWorld``. Two facts in the
source name that file, and the JS/TS analyzer turns each into a
UsageContext on the preload FILE symbol that electron.yaml's usage rows
read (audit 0027, WI-jilik):

* a ``contextBridge.exposeInMainWorld`` / ``exposeInIsolatedWorld`` call,
  anywhere in the file, on ``contextBridge`` or ``<x>.contextBridge``;
* a ``preload:`` property of a ``webPreferences:`` object whose value is a
  literal path off ``__dirname`` (``path.join`` / ``path.resolve`` /
  ``join`` / ``resolve`` of string segments, ``__dirname + '...'``, or a
  template ``${__dirname}/...``) naming a file the analysis holds.

A path the source does not spell (a variable, a bundler constant) or one
that names no analysed file gives nothing. The entry (``electron_preload``)
replaces the ``script_module`` entry the same file would get.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.entrypoints import EntrypointKind, detect_entrypoints
from hypergumbo_core.framework_patterns import clear_pattern_cache, enrich_symbols
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _file_id(result, rel: str) -> str:
    return next(s.id for s in result.symbols if s.kind == "file" and s.path.endswith(rel))


def _preload_ucs(result):
    return [c for c in result.usage_contexts
            if c.context_name in ("contextBridge.exposeInMainWorld",
                                  "contextBridge.exposeInIsolatedWorld",
                                  "webPreferences.preload")]


def test_a_context_bridge_exposure_marks_its_file(tmp_path: Path) -> None:
    (tmp_path / "preload.js").write_text(
        "const { contextBridge, ipcRenderer } = require('electron');\n"
        "contextBridge.exposeInMainWorld('api', {\n"
        "  ping: () => ipcRenderer.invoke('ping'),\n"
        "});\n"
    )
    result = analyze_javascript(tmp_path)
    ucs = _preload_ucs(result)
    assert [(c.kind, c.context_name, c.position, c.symbol_ref) for c in ucs] == [
        ("call", "contextBridge.exposeInMainWorld", "caller",
         _file_id(result, "preload.js"))]


def test_the_qualified_and_isolated_forms_inside_a_function(tmp_path: Path) -> None:
    (tmp_path / "pre.ts").write_text(
        "import * as electron from 'electron';\n"
        "function expose(): void {\n"
        "  electron.contextBridge.exposeInIsolatedWorld(1, 'api', {});\n"
        "  electron.contextBridge.exposeInMainWorld('api', {});\n"
        "  electron.contextBridge.exposeInMainWorld('more', {});\n"
        "}\n"
        "expose();\n"
    )
    result = analyze_javascript(tmp_path)
    file_id = _file_id(result, "pre.ts")
    assert sorted((c.context_name, c.symbol_ref) for c in _preload_ucs(result)) == [
        ("contextBridge.exposeInIsolatedWorld", file_id),
        ("contextBridge.exposeInMainWorld", file_id),
    ]


def test_other_calls_named_like_it_are_not_a_bridge(tmp_path: Path) -> None:
    (tmp_path / "a.js").write_text(
        "exposeInMainWorld('api', {});\n"
        "bridge.exposeInMainWorld('api', {});\n"
        "contextBridge.other('api');\n"
    )
    assert _preload_ucs(analyze_javascript(tmp_path)) == []


def test_each_literal_preload_path_form_names_its_file(tmp_path: Path) -> None:
    (tmp_path / "src" / "main").mkdir(parents=True)
    (tmp_path / "src" / "preload").mkdir()
    for name in ("a.js", "b.js", "c.js", "d.js", "e.js"):
        (tmp_path / "src" / "main" / name).write_text("console.log(1);\n")
    (tmp_path / "src" / "preload" / "index.js").write_text("console.log(1);\n")
    (tmp_path / "src" / "main" / "main.js").write_text(
        "const path = require('path');\n"
        "const { join, resolve } = require('path');\n"
        "const { BrowserWindow } = require('electron');\n"
        "new BrowserWindow({ webPreferences: { preload: path.join(__dirname, 'a.js') } });\n"
        "new BrowserWindow({ webPreferences: { preload: resolve(__dirname, 'b.js') } });\n"
        "new BrowserWindow({ webPreferences: { preload: __dirname + '/c.js' } });\n"
        "new BrowserWindow({ webPreferences: { 'preload': `${__dirname}/d.js` } });\n"
        "new BrowserWindow({ webPreferences: { preload: path.join(__dirname, `e.js`) } });\n"
        "new BrowserWindow({ webPreferences: { preload: path.join(__dirname, 'e.js') } });\n"
        "const opts = { webPreferences: { preload: join(__dirname, '..', 'preload', 'index.js') } };\n"
        "new BrowserWindow(opts);\n"
    )
    result = analyze_javascript(tmp_path)
    got = sorted((c.symbol_ref, c.position, c.metadata["preload_path"])
                 for c in _preload_ucs(result))
    assert got == sorted(
        (_file_id(result, rel), "webPreferences.preload", rel)
        for rel in ("src/main/a.js", "src/main/b.js", "src/main/c.js",
                    "src/main/d.js", "src/main/e.js", "src/preload/index.js"))
    assert all(c.kind == "call" and c.context_name == "webPreferences.preload"
               for c in _preload_ucs(result))


def test_a_path_the_source_does_not_spell_names_nothing(tmp_path: Path) -> None:
    (tmp_path / "there.js").write_text("console.log(1);\n")
    (tmp_path / "main.js").write_text(
        "const path = require('path');\n"
        "const p = path.join(__dirname, 'there.js');\n"
        "new BrowserWindow({ webPreferences: { preload: p } });\n"
        "new BrowserWindow({ webPreferences: { preload: MAIN_WINDOW_PRELOAD_WEBPACK_ENTRY } });\n"
        "new BrowserWindow({ webPreferences: { preload: path.join(__dirname, 'missing.js') } });\n"
        "new BrowserWindow({ webPreferences: { preload: path.join(__dirname, name) } });\n"
        "new BrowserWindow({ webPreferences: { preload: path.join(root, 'there.js') } });\n"
        "new BrowserWindow({ webPreferences: { preload: path.resolve(__dirname, '/abs/there.js') } });\n"
        "new BrowserWindow({ webPreferences: { preload: path.join(__dirname, '..', '..', 'x.js') } });\n"
        "new BrowserWindow({ webPreferences: { preload: path.format(__dirname, 'there.js') } });\n"
        "new BrowserWindow({ webPreferences: { preload: `${root}/there.js` } });\n"
        "new BrowserWindow({ webPreferences: { preload: `${__dirname}/${name}` } });\n"
        "new BrowserWindow({ webPreferences: { preload: root + '/there.js' } });\n"
        "new BrowserWindow({ webPreferences: { preload: 'there.js' } });\n"
        "new BrowserWindow({ webPreferences: { preload: path.join() } });\n"
        "new BrowserWindow({ webPreferences: { preload: __dirname + 'there.js' } });\n"
        "new BrowserWindow({ webPreferences: { preload: `${__dirname}there.js` } });\n"
        "new BrowserWindow({ webPreferences: { preload: `/x/${__dirname}/there.js` } });\n"
        "configure({ preload: __dirname + '/there.js' });\n"
        "new BrowserWindow({ options: { preload: __dirname + '/there.js' } });\n"
        "new BrowserWindow({ webPreferences });\n"
    )
    assert _preload_ucs(analyze_javascript(tmp_path)) == []


def test_the_preload_file_is_one_electron_preload_entry(tmp_path: Path) -> None:
    """The audit's probe4_electron: the bridge file and the file the window
    names are electron_preload, never also script_module; the renderer
    stays a script_module and main.js the electron_main."""
    (tmp_path / "package.json").write_text(
        '{"name":"eprobe","main":"main.js","dependencies":{"electron":"^30.0.0"}}')
    (tmp_path / "main.js").write_text(
        "const { app, BrowserWindow, ipcMain } = require('electron');\n"
        "function createWindow() {\n"
        "  const win = new BrowserWindow({ webPreferences: { preload: __dirname + '/preload.js' } });\n"
        "  win.loadFile('index.html');\n"
        "}\n"
        "app.whenReady().then(createWindow);\n"
        "ipcMain.handle('ping', () => 'pong');\n"
    )
    (tmp_path / "preload.js").write_text(
        "const { contextBridge, ipcRenderer } = require('electron');\n"
        "contextBridge.exposeInMainWorld('api', {\n"
        "  ping: () => ipcRenderer.invoke('ping'),\n"
        "});\n"
    )
    (tmp_path / "other_preload.js").write_text("window.x = require('os').platform();\n")
    (tmp_path / "bridge_only.js").write_text(
        "const electron = require('electron');\n"
        "electron.contextBridge.exposeInMainWorld('os', { p: () => process.platform });\n"
    )
    (tmp_path / "second.js").write_text(
        "const path = require('path');\n"
        "new BrowserWindow({ webPreferences: { preload: path.join(__dirname, 'other_preload.js') } });\n"
    )
    (tmp_path / "renderer.js").write_text(
        "async function go() { const r = await window.api.ping(); console.log(r); }\n"
        "go();\n"
    )
    result = analyze_javascript(tmp_path)
    clear_pattern_cache()
    enrich_symbols(result.symbols, {"electron"}, result.usage_contexts)
    entries = detect_entrypoints(result.symbols, result.edges)
    by_path: dict[str, list[EntrypointKind]] = {}
    paths = {s.id: s.path for s in result.symbols}
    for ep in entries:
        by_path.setdefault(Path(paths[ep.symbol_id]).name, []).append(ep.kind)
    assert by_path["preload.js"] == [EntrypointKind.ELECTRON_PRELOAD]
    assert by_path["other_preload.js"] == [EntrypointKind.ELECTRON_PRELOAD]
    # A bridge file no window names in a literal: the contextBridge row alone.
    assert by_path["bridge_only.js"] == [EntrypointKind.ELECTRON_PRELOAD]
    assert by_path["main.js"] == [EntrypointKind.ELECTRON_MAIN]
    assert by_path["renderer.js"] == [EntrypointKind.SCRIPT_MODULE]
    preload = next(ep for ep in entries if paths[ep.symbol_id].endswith("/preload.js"))
    assert preload.label == f"Electron preload script ({paths[preload.symbol_id]})"
    assert preload.confidence == 0.90
    assert (preload.meta or {}).get("evidence_type") == "framework_pattern"
