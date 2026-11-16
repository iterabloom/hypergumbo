# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-jomik: a call through an EXTERNAL module's binding cannot reach the repo.

Phase 1 of WI-jomik measured the JS/TS wrong edges by mechanism, and the
largest class was a member call bound by its bare member name with the
receiver ignored: knex's ``path.join(dir, stub)`` (``const path =
require('path')``) bound to ``Builder.join``, ``rp.delete(...)``
(``request-promise-native``) to ``Builder.delete``, express's
``path.resolve(...)`` to ``var resolve`` in ``lib/view.js``, and jotai's
``subject.subscribe(...)`` (``new Subject()`` from ``rxjs``) to a test file's
object-literal ``subscribe``. The namespace-import case passed the module as a
``path_hint``, but ``NameResolver`` applies a hint only to choose AMONG suffix
candidates, so a lone in-repo ``*.join`` was returned whatever the hint said.

The rule is a call SHAPE: a callee reached through a binding of a module the
repo positively does not define (``_bare_specifier_is_external``: a Node core
module, or a package the importing ``package.json`` declares from a registry)
is that module's, never an in-repo symbol. It gets the unresolved edge the
module's other unmatched members already get, with the module as its hint, so
io-boundaries still classifies it. A relative import, a workspace package or
an undeclared bare specifier (a tsconfig alias looks exactly like one) is not
shown external and keeps the in-repo resolution: the controls below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_BUILDER = (
    "class Builder {\n"
    "  join(other) { return other; }\n"
    "  delete() { return 1; }\n"
    "}\n"
    "module.exports = { Builder };\n"
)
_VIEW = (
    "var path = require('node:path');\n"
    "var resolve = path.resolve;\n"
    "function lookup(name) { return resolve(name); }\n"
    "module.exports = lookup;\n"
)
_STORAGE = (
    "const dummyStorage = {\n"
    "  subscribe(key, cb) { return cb; },\n"
    "};\n"
    "export { dummyStorage };\n"
)


def _in_repo(dst: str) -> bool:
    """A dst naming a file under the fixture's ``src/`` (the direct analyzer
    call writes absolute paths into ids)."""
    return "/src/" in dst.split(":")[1] and not dst.endswith(":unresolved")


def _calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str],
) -> dict[str, set[str]]:
    """``{caller name: {callee dst id}}`` for every calls edge.

    The cache is the test's own: a shared one keyed by file content would
    answer for code this run did not analyse."""
    pytest.importorskip("tree_sitter_javascript")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from hypergumbo_lang_mainstream.js_ts import analyze_javascript

    (tmp_path / "package.json").write_text(json.dumps({
        "name": "fx", "version": "1.0.0",
        "dependencies": {"rxjs": "7", "request-promise-native": "1"},
    }))
    for name, text in {
        "src/builder.js": _BUILDER, "src/view.js": _VIEW,
        "src/storage.js": _STORAGE, **files,
    }.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    result = analyze_javascript(tmp_path)
    by_id = {s.id: s.name for s in result.symbols}
    out: dict[str, set[str]] = {}
    for e in result.edges:
        if e.edge_type == "calls" and e.src in by_id:
            out.setdefault(by_id[e.src], set()).add(e.dst)
    return out


@pytest.mark.parametrize("code,caller,member,module", [
    ("const path = require('path');\n"
     "function stub(d, s) { return path.join(d, s); }\n",
     "stub", "join", "path"),
    ("const rp = require('request-promise-native');\n"
     "async function hit() { await rp.delete({ uri: 'x' }); }\n",
     "hit", "delete", "request-promise-native"),
    ("import * as path from 'node:path';\n"
     "export function all(p) { return path.resolve(p); }\n",
     "all", "resolve", "path"),
    ("const { resolve } = require('node:path');\n"
     "function destr(p) { return resolve(p); }\n",
     "destr", "resolve", "path"),
    ("import { Subject } from 'rxjs';\n"
     "export function watch(s) { const subject = new Subject(); subject.subscribe(s); }\n",
     "watch", "subscribe", "rxjs"),
], ids=["require-core", "require-declared-pkg", "namespace-core",
        "destructured-require", "constructed-from-pkg"])
def test_a_call_through_an_external_binding_does_not_reach_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    code: str, caller: str, member: str, module: str,
) -> None:
    calls = _calls(tmp_path, monkeypatch, {"src/caller.js": code})
    dsts = calls.get(caller, set())
    assert not [d for d in dsts if _in_repo(d)], dsts
    hint = [d for d in dsts if d.endswith(f":{member}:unresolved")]
    assert hint and hint[0].split(":")[1].startswith(module), dsts


@pytest.mark.parametrize("code,caller,target", [
    ("const b = require('./builder');\n"
     "function rel(x) { const k = new b.Builder(); return k.join(x); }\n",
     "rel", "Builder.join"),
    ("import * as alias from '@/builder';\n"
     "export function aliased(x) { return alias.join(x); }\n",
     "aliased", "Builder.join"),
    ("const { lookup } = require('./view');\n"
     "function local(n) { return lookup(n); }\n",
     "local", "lookup"),
], ids=["relative-receiver", "undeclared-bare-alias", "relative-destructured"])
def test_an_in_tree_binding_still_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    code: str, caller: str, target: str,
) -> None:
    calls = _calls(tmp_path, monkeypatch, {"src/caller.js": code})
    assert any(_in_repo(d) and f":{target}:" in d
               for d in calls.get(caller, set())), calls.get(caller)


def test_an_untyped_receivers_weak_in_file_pick_is_still_deferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback the external rule sits in front of is unchanged (INV-fahub):
    an untyped ``obj.run()`` whose one in-file candidate belongs to ANOTHER
    class is withheld for the inherited-calls walker, not bound."""
    calls = _calls(tmp_path, monkeypatch, {
        "src/a.js": (
            "class Other { run() { return 1; } }\n"
            "class Mine { go(obj) { return obj.run(); } }\n"
        ),
        "src/b.js": "class Third { run() { return 2; } }\n",
    })
    dsts = calls.get("Mine.go", set())
    assert not [d for d in dsts if _in_repo(d)], dsts
    assert any(d.endswith(":run:unresolved") for d in dsts), dsts
