# SPDX-License-Identifier: AGPL-3.0-or-later
"""The library-export demotion spares a JS/TS package that declares ``exports``.

Any production semantic entry of a language (a route, a server, a main, a
command) demotes every library export of that language x0.1. The one
exception: the exports of a JavaScript / TypeScript package whose nearest
``package.json`` declares an ``exports`` map are not demoted. That map is the
package's own statement of what consumers may import (Node refuses any other
subpath), so those symbols are its API whether or not the package also starts
a server (vite: its dev server's WebSocket server and its exported API live in
one package).

These tests write real ``package.json`` files: the rule reads the manifest from
the repository, so a list recomputed without the repository root
(``repo_root=None``) keeps the plain demotion.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hypergumbo_core import entrypoints as entrypoints_module
from hypergumbo_core.entrypoints import (
    EntrypointKind,
    detect_entrypoint_candidates,
    detect_entrypoints,
)
from hypergumbo_core.ir import Span, Symbol

_ROUTE = {"concepts": [{"concept": "route", "framework": "node-http",
                        "method": "ALL", "path": "/"}]}
_EXPORT = {"concepts": [{"concept": "library_export"}]}


def _sym(name: str, path: str, *, kind: str = "function", language: str = "typescript",
         meta: dict | None = None, tier: int = 1) -> Symbol:
    return Symbol(
        id=f"{language}:{path}:1-3:{name}:{kind}", name=name, kind=kind,
        language=language, path=path,
        span=Span(start_line=1, end_line=3, start_col=0, end_col=1),
        origin="test", origin_run_id="uuid:test", meta=meta,
        supply_chain_tier=tier,
    )


def _route(path: str, language: str = "typescript") -> Symbol:
    """A production server entry (node-http ``createServer``) in ``path``."""
    return _sym(path, path, kind="file", language=language, meta=_ROUTE)


def _export(name: str, path: str, language: str = "typescript", tier: int = 1) -> Symbol:
    return _sym(name, path, language=language, meta=_EXPORT, tier=tier)


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def _export_scores(nodes: list[Symbol], repo_root: Path | None) -> dict[str, float]:
    """rank_score of every library export candidate, by symbol name."""
    names = {n.id: n.name for n in nodes}
    return {
        names[ep.symbol_id]: ep.rank_score
        for ep in detect_entrypoint_candidates(nodes, [], repo_root=repo_root)
        if ep.kind == EntrypointKind.LIBRARY_EXPORT
    }


_EXPORTS_FORMS = {
    "string": '"./dist/index.js"',
    "subpath object": '{".": "./dist/index.js", "./client": "./dist/client.js"}',
    "conditions object": '{"import": "./dist/index.mjs", "require": "./dist/index.cjs"}',
    "subpath with nested conditions": (
        '{".": {"import": {"types": "./d.ts", "default": "./dist/index.js"}}}'),
    "array": '["./dist/index.js"]',
}


class TestExportsMapKeepsExports:

    @pytest.mark.parametrize("form", sorted(_EXPORTS_FORMS))
    def test_a_package_declaring_exports_keeps_its_exports_beside_a_server(
        self, tmp_path: Path, form: str,
    ) -> None:
        """vite with the aliased createServer read: its server is production
        TypeScript, and its package declares ``exports``. Every form of the
        field counts: Node reads each as the package's public surface."""
        _write(tmp_path, "packages/lib/package.json",
               '{"name": "lib", "exports": %s}' % _EXPORTS_FORMS[form])
        nodes = [_route("packages/lib/src/server/ws.ts"),
                 _export("createServer", "packages/lib/src/index.ts")]
        scores = _export_scores(nodes, tmp_path)
        assert scores["createServer"] == pytest.approx(0.75)
        listed = {e.symbol_id for e in detect_entrypoints(nodes, [], repo_root=tmp_path)}
        assert nodes[1].id in listed

    def test_only_main_does_not_keep_the_exports(self, tmp_path: Path) -> None:
        """``main`` cannot tell a library from an application (pm2, thc and
        gemini-cli's cli declare it), so it leaves the demotion in place."""
        _write(tmp_path, "packages/lib/package.json",
               '{"name": "lib", "main": "./dist/index.js", "types": "./d.ts"}')
        nodes = [_route("packages/lib/src/server/ws.ts"),
                 _export("createServer", "packages/lib/src/index.ts")]
        assert _export_scores(nodes, tmp_path)["createServer"] == pytest.approx(0.075)
        listed = {e.symbol_id for e in detect_entrypoints(nodes, [], repo_root=tmp_path)}
        assert nodes[1].id not in listed

    def test_a_sibling_package_without_exports_stays_demoted(self, tmp_path: Path) -> None:
        """The exemption is per package: one package's ``exports`` does not
        reach a sibling package, nor files above it."""
        _write(tmp_path, "package.json", '{"name": "root", "private": true}')
        _write(tmp_path, "packages/lib/package.json", '{"exports": "./dist/index.js"}')
        _write(tmp_path, "packages/app/package.json", '{"main": "./dist/index.js"}')
        nodes = [
            _route("packages/app/src/server.ts"),
            _export("libApi", "packages/lib/src/index.ts"),
            _export("appApi", "packages/app/src/index.ts"),
            _export("rootApi", "src/root_api.ts"),
        ]
        scores = _export_scores(nodes, tmp_path)
        assert scores["libApi"] == pytest.approx(0.75)
        assert scores["appApi"] == pytest.approx(0.075)
        assert scores["rootApi"] == pytest.approx(0.075)

    def test_the_nearest_package_json_decides(self, tmp_path: Path) -> None:
        """A nested package.json without ``exports`` is its own package (Node's
        package scope): the outer package's ``exports`` does not reach it."""
        _write(tmp_path, "packages/lib/package.json", '{"exports": "./dist/index.js"}')
        _write(tmp_path, "packages/lib/playground/package.json", '{"private": true}')
        nodes = [
            _route("packages/lib/src/server.ts"),
            _export("libApi", "packages/lib/src/deep/nested/index.ts"),
            _export("demo", "packages/lib/playground/src/demo.ts"),
        ]
        scores = _export_scores(nodes, tmp_path)
        assert scores["libApi"] == pytest.approx(0.75)
        assert scores["demo"] == pytest.approx(0.075)

    def test_non_js_ts_exports_in_the_package_are_still_demoted(
        self, tmp_path: Path,
    ) -> None:
        """The field speaks for the package's JavaScript / TypeScript only: a
        Python export with a production Python main is demoted, even inside a
        directory whose package.json declares ``exports``."""
        _write(tmp_path, "packages/lib/package.json", '{"exports": "./dist/index.js"}')
        nodes = [
            _route("packages/lib/src/server.ts"),
            _export("libApi", "packages/lib/src/index.ts"),
            _sym("main", "packages/lib/py/app.py", language="python",
                 meta={"concepts": [{"concept": "main_function"}]}),
            _export("helper", "packages/lib/py/helper.py", language="python"),
        ]
        scores = _export_scores(nodes, tmp_path)
        assert scores["libApi"] == pytest.approx(0.75)
        assert scores["helper"] == pytest.approx(0.075)

    def test_javascript_exports_count_too(self, tmp_path: Path) -> None:
        _write(tmp_path, "package.json", '{"exports": {".": "./index.js"}}')
        nodes = [_route("lib/server.js", language="javascript"),
                 _export("api", "lib/index.js", language="javascript")]
        assert _export_scores(nodes, tmp_path)["api"] == pytest.approx(0.75)

    def test_a_vendored_export_stays_demoted(self, tmp_path: Path) -> None:
        """The exemption protects the package's own API; a vendored (tier 3)
        export is nobody's API here and keeps every demotion."""
        _write(tmp_path, "packages/lib/package.json", '{"exports": "./dist/index.js"}')
        nodes = [_route("packages/lib/src/server.ts"),
                 _export("bundled", "packages/lib/vendor/x.ts", tier=3)]
        with_root = _export_scores(nodes, tmp_path)["bundled"]
        assert with_root == pytest.approx(_export_scores(nodes, None)["bundled"])
        assert with_root < 0.75 * 0.3

    @pytest.mark.parametrize("manifest", [
        '{"name": "lib", "exports": "./dist/index.js",}',  # trailing comma
        "{not json",
        '["exports"]',
        '"exports"',
        '{"exports": null}',
        '{"exports": {}}',
        '{"exports": ""}',
        "",
    ])
    def test_a_malformed_or_empty_declaration_does_not_exempt(
        self, tmp_path: Path, manifest: str,
    ) -> None:
        """Node cannot load a malformed manifest, and an empty or null
        ``exports`` exposes nothing: neither declares an API, so the export
        is demoted, and nothing raises."""
        _write(tmp_path, "packages/lib/package.json", manifest)
        nodes = [_route("packages/lib/src/server.ts"),
                 _export("libApi", "packages/lib/src/index.ts")]
        assert _export_scores(nodes, tmp_path)["libApi"] == pytest.approx(0.075)

    def test_an_unreadable_package_json_does_not_exempt(self, tmp_path: Path) -> None:
        """A ``package.json`` that is a directory cannot be read: no exemption."""
        (tmp_path / "packages" / "lib" / "package.json").mkdir(parents=True)
        nodes = [_route("packages/lib/src/server.ts"),
                 _export("libApi", "packages/lib/src/index.ts")]
        assert _export_scores(nodes, tmp_path)["libApi"] == pytest.approx(0.075)

    def test_without_the_repository_the_plain_demotion_applies(
        self, tmp_path: Path,
    ) -> None:
        """No root, no manifest to read: the list is today's."""
        _write(tmp_path, "packages/lib/package.json", '{"exports": "./dist/index.js"}')
        nodes = [_route("packages/lib/src/server.ts"),
                 _export("libApi", "packages/lib/src/index.ts")]
        assert _export_scores(nodes, None)["libApi"] == pytest.approx(0.075)

    def test_no_package_json_up_to_the_root_does_not_exempt(self, tmp_path: Path) -> None:
        """The walk stops at the repository root: a package.json above the
        analysed tree is not this repository's declaration."""
        _write(tmp_path, "package.json", '{"exports": "./index.js"}')
        repo = tmp_path / "repo"
        _write(repo, "src/keep.txt", "")
        nodes = [_route("src/server.ts"), _export("api", "src/index.ts")]
        assert _export_scores(nodes, repo)["api"] == pytest.approx(0.075)

    def test_no_server_no_demotion_either_way(self, tmp_path: Path) -> None:
        """A pure library keeps its exports with or without the field."""
        _write(tmp_path, "package.json", '{"main": "./index.js"}')
        nodes = [_export("api", "src/index.ts")]
        assert _export_scores(nodes, tmp_path)["api"] == pytest.approx(0.75)


def test_every_production_detection_call_passes_the_repository_root() -> None:
    """The exemption reads the manifest from the repository, so a caller that
    omits ``repo_root`` silently lists the plain demotion. Every call in the
    package's source passes it (run, slice, dead-code-maybe, sketch)."""
    src = Path(entrypoints_module.__file__).parent
    calls = []
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("detect_entrypoints",
                                         "detect_entrypoint_candidates")):
                calls.append((path.name, node.lineno,
                              any(k.arg == "repo_root" for k in node.keywords)))
    assert len(calls) >= 5
    assert [c for c in calls if not c[2]] == []
