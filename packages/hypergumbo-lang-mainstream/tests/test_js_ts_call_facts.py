# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the JS/TS analyzer binds itself, and what it hands the shared pass (ADR-0064).

The analyzer binds a call only on evidence JavaScript itself gives and that it
has in its own file: a declaration the call site sees by position
(``lexical_scope``), a member of the enclosing class (``this.m()``,
``own_type``) and a member of the receiver's type when this file declares the
type (``receiver_type``). Every other call is emitted unresolved with the facts
the ``inherited_calls`` linker reads, and the linker decides from the caller's
file's import table (``AnalysisResult.import_scopes``), whose modules are FILES
(the js-module linker's ``SpecifierResolver`` says which):

* nothing but the LOCAL name, for a bare call to a name an import of a
  repository file binds (the table is keyed by it);
* ``qualifier``: a namespace or default binding's member (``util.helper()``),
  a class this file declares or imports by name (``Util.helper()``), a
  namespace binding's class (``ns.Util.helper()``);
* ``receiver_type_hint``: the receiver's type (a ``new``, a parameter
  annotation, a constructor-injected property, the declared return type of a
  call this file binds) when this file does not declare the member;
* ``enclosing_class`` with its id, for ``this.m()`` the class does not
  declare, and ``super_receiver`` for ``super.m()``.

The first half pins the table and what the analyzer emits, route by route.
The second half runs the whole pipeline for the cases the shared pass exists
for; every multi-file case there has a namesake in another file.
"""
# covers: packages/hypergumbo-lang-mainstream/src/hypergumbo_lang_mainstream/js_ts.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.import_scope import MODULES_ARE_FILES
from hypergumbo_core.ir import Edge
from hypergumbo_lang_mainstream.js_ts import analyze_javascript


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _calls(root: Path, files: dict[str, str], caller: str) -> list[Edge]:
    """The ``calls`` edges the analyzer alone emits from the symbol named ``caller``."""
    result = analyze_javascript(_write(root, files))
    src = next(s.id for s in result.symbols if s.name == caller)
    return [e for e in result.edges if e.edge_type == "calls" and e.src == src]


def _facts(edge: Edge) -> dict[str, Any]:
    meta = edge.meta or {}
    return {k: meta[k] for k in (
        "receiver_type_hint", "qualifier", "enclosing_class", "super_receiver", "call_construct",
    ) if k in meta}


def _stub(edges: list[Edge], callee: str) -> Edge:
    found = [e for e in edges if not e.is_resolved and e.dst.split(":")[-2] == callee]
    assert len(found) == 1, (callee, [(e.dst, e.meta) for e in edges])
    return found[0]


# --------------------------------------------------------------- the table


def test_every_file_hands_over_a_table_of_files(tmp_path: Path) -> None:
    result = analyze_javascript(_write(tmp_path, {
        "src/a.ts": (
            "import { helper as h, gone } from './util';\n"
            "import * as u from './util';\n"
            "import Svc from './svc';\n"
            "import anon from './anon';\n"
            "import { chunk } from 'lodash';\n"
            "import * as fs from 'fs';\n"
            "import { x } from './missing';\n"
            "import data from './data.json';\n"
            "const { tool: t } = require('./tools');\n"
            "const tools = require('./tools');\n"
        ),
        "src/util.ts": "export function helper() { return 1; }\n",
        "src/svc.ts": "export default class Service { run() {} }\n",
        "src/anon.ts": "export default () => 1;\n",
        "src/tools.js": "function tool() {}\nmodule.exports = { tool };\n",
        "src/data.json": "{}\n",
        "src/empty.ts": "export const k = 1;\n",
    }))
    assert set(result.import_scopes) == {
        "src/a.ts", "src/util.ts", "src/svc.ts", "src/anon.ts", "src/tools.js", "src/empty.ts",
    }
    assert all(s.module_kind == MODULES_ARE_FILES for s in result.import_scopes.values())
    scope = result.import_scopes["src/a.ts"]
    named = {local: (scope.named(local).module, scope.named(local).name)
             for local in ("h", "gone", "Svc", "t") if scope.named(local) is not None}
    assert named == {
        "h": ("src/util.ts", "helper"),
        # Recorded as written: the file does not declare it, the lookup finds nothing.
        "gone": ("src/util.ts", "gone"),
        # A default import binds the default export's declaration.
        "Svc": ("src/svc.ts", "Service"),
        "t": ("src/tools.js", "tool"),
    }
    assert (scope.namespace("u"), scope.namespace("tools")) == ("src/util.ts", "src/tools.js")
    # Not files of the repository, or not one this analyzer read; an
    # anonymous default export names no declaration.
    for local in ("chunk", "fs", "x", "data", "anon"):
        assert scope.named(local) is None and scope.namespace(local) is None, local
    assert result.import_scopes["src/empty.ts"].named("k") is None


def test_a_component_s_script_blocks_share_one_table(tmp_path: Path) -> None:
    result = analyze_javascript(_write(tmp_path, {
        "src/Card.svelte": (
            "<script context=\"module\">\n  import * as util from './util';\n</script>\n"
            "<script>\n  import { helper } from './util';\n  util.helper(); helper();\n</script>\n"
        ),
        "src/util.ts": "export function helper() { return 1; }\n",
    }))
    scope = result.import_scopes["src/Card.svelte"]
    assert scope.namespace("util") == "src/util.ts"
    assert scope.named("helper").module == "src/util.ts"


def _reexports(scope: Any) -> dict[str, tuple[str, str]]:
    return {k: (v.module, v.name) for k, v in scope._reexports.items()}


def test_a_barrel_file_records_what_it_passes_on(tmp_path: Path) -> None:
    result = analyze_javascript(_write(tmp_path, {
        "src/lib/index.ts": (
            "export * from './impl';\n"
            "export { helper as aid, Conn } from './impl';\n"
            "export { default as Store } from './def';\n"
            "export * as ns from './impl';\n"
            "export { chunk } from 'lodash';\n"
            "export * from 'rxjs';\n"
            "export { default as Anon } from './anon';\n"
            "function local() {}\n"
            "export { local as named, local };\n"
        ),
        "src/lib/impl.ts": "export function helper() {}\nexport class Conn {}\n",
        "src/lib/def.ts": "export default class Store {}\n",
        "src/lib/anon.ts": "export default () => 1;\n",
        "src/lib/again.ts": "export { default } from './def';\n",
        "src/lib/conn.ts": "export { Conn as default } from './impl';\n",
        "src/main.ts": "import D from './lib/again';\nimport C from './lib/conn';\n",
    }))
    scope = result.import_scopes["src/lib/index.ts"]
    assert _reexports(scope) == {
        "aid": ("src/lib/impl.ts", "helper"),
        "Conn": ("src/lib/impl.ts", "Conn"),
        # A re-exported default names the declaration the default import names.
        "Store": ("src/lib/def.ts", "Store"),
        # A local binding exported under another name.
        "named": ("src/lib/index.ts", "local"),
    }
    # ``export *`` of a repository file; a library's is left out, and so is
    # ``export * as ns`` (the table has no form for it) and an anonymous default.
    assert scope.passed_on_modules == ("src/lib/impl.ts",)
    assert _reexports(result.import_scopes["src/lib/again.ts"]) == {
        "default": ("src/lib/def.ts", "Store"),
    }
    assert _reexports(result.import_scopes["src/lib/conn.ts"]) == {
        "default": ("src/lib/impl.ts", "Conn"),
    }
    # A default import of a file whose default is re-exported binds ``default``,
    # which that file's own table follows.
    main = result.import_scopes["src/main.ts"]
    assert {k: (main.named(k).module, main.named(k).name) for k in ("D", "C")} == {
        "D": ("src/lib/again.ts", "default"), "C": ("src/lib/conn.ts", "default"),
    }


def test_commonjs_exports_in_the_table(tmp_path: Path) -> None:
    result = analyze_javascript(_write(tmp_path, {
        "src/client.js": "class Client { run() {} }\nmodule.exports = Client;\n",
        "src/make.js": "function make() {}\nexports = module.exports = make;\n",
        "src/obj.js": "const obj = { run() {} };\nmodule.exports = obj;\n",
        "src/helpers.js": (
            "function helper() {}\nfunction other() {}\n"
            "exports.b = helper;\nmodule.exports.c = other;\nexports.other = other;\n"
        ),
        "src/table.js": "function helper() {}\nmodule.exports = { n: helper, helper, m() {} };\n",
        "src/esm.js": "export default class Esm {}\n",
        "src/main.js": (
            "const Client = require('./client');\n"
            "const make = require('./make');\n"
            "const obj = require('./obj');\n"
            "const esm = require('./esm');\n"
            "const { n } = require('./table');\n"
            "import C2 from './client';\n"
        ),
    }))
    scope = result.import_scopes["src/main.js"]
    named = {k: (v.module, v.name) for k, v in scope._named.items()}
    assert named == {
        # ``module.exports = <a declared class or function>``: the value is it.
        "Client": ("src/client.js", "Client"),
        "make": ("src/make.js", "make"),
        "n": ("src/table.js", "n"),
        # An ES default import of a CommonJS module receives module.exports.
        "C2": ("src/client.js", "Client"),
    }
    # A variable assigned whole, and an ES module, are module objects.
    assert (scope.namespace("obj"), scope.namespace("esm")) == ("src/obj.js", "src/esm.js")
    assert _reexports(result.import_scopes["src/helpers.js"]) == {
        "b": ("src/helpers.js", "helper"), "c": ("src/helpers.js", "other"),
    }
    assert _reexports(result.import_scopes["src/table.js"]) == {"n": ("src/table.js", "helper")}


# ------------------------------------------------------ what the analyzer emits


def test_a_bare_call_to_an_imported_function_goes_out_under_its_local_name(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "src/a.js": "import { helper as h } from './util';\nfunction run() { h(); }\n",
        "src/util.js": "export function helper() {}\n",
    }, "run")
    stub = _stub(edges, "h")
    assert stub.dst == "javascript:util:0-0:h:unresolved"
    assert _facts(stub) == {}


def test_a_name_imported_from_a_library_keeps_its_canonical_stub(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "src/a.js": "import { readFile as rf } from 'fs';\nfunction run() { rf(); }\n",
        "src/b.js": "function readFile() {}\nfunction rf() {}\n",
    }, "run")
    assert [e.dst for e in edges] == ["javascript:fs:0-0:readFile:unresolved"]


def test_this_call_binds_the_enclosing_class_member_of_this_file(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "src/a.ts": "class A {\n  m() {}\n  go() { this.m(); this.inherited(); }\n}\n",
        "src/b.ts": "class A {\n  m() {}\n}\n",
    }, "A.go")
    (bound,) = [e for e in edges if e.is_resolved]
    assert bound.dst.split(":")[1].endswith("src/a.ts")
    assert bound.meta["resolved_by"] == "own_type"
    stub = _stub(edges, "inherited")
    assert _facts(stub) == {"enclosing_class": "A", "call_construct": "method"}
    assert stub.meta["enclosing_class_id"].endswith("src/a.ts:1-4:A:class")


def test_super_call_starts_at_the_parent(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "src/a.ts": "class B { m() {} }\nclass C extends B {\n  m() { super.m(); }\n}\n",
    }, "C.m")
    assert _facts(_stub(edges, "m")) == {
        "enclosing_class": "C", "super_receiver": True, "call_construct": "method",
    }


def test_this_outside_a_class_is_the_placeholder(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {"src/a.js": "function f() { this.go(); }\nclass K { go() {} }\n"}, "f")
    assert _facts(_stub(edges, "go")) == {"call_construct": "method"}


def test_typed_receivers_bind_in_this_file_and_carry_their_type_otherwise(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "src/a.ts": (
            "import { Remote } from './remote';\n"
            "class Own { run() {} }\n"
            "class C {\n"
            "  constructor(private own: Own, private remote: Remote) {}\n"
            "  go(p: Remote) {\n"
            "    this.own.run(); this.remote.run();\n"
            "    const o = new Own(); o.run(); o.base();\n"
            "    p.run();\n"
            "  }\n"
            "}\n"
        ),
        "src/remote.ts": "export class Remote { run() {} }\n",
    }, "C.go")
    bound = sorted((e.dst.split(":")[-2], e.evidence_type) for e in edges if e.is_resolved)
    assert bound == [("Own.run", "ast_method_this_property"), ("Own.run", "ast_method_type_inferred")]
    hints = sorted(
        (e.dst.split(":")[-2], e.meta.get("receiver_type_hint")) for e in edges if not e.is_resolved
    )
    assert hints == [("base", "Own"), ("run", "Remote"), ("run", "Remote")]


def test_a_receiver_that_names_a_type_or_a_module_is_a_qualifier(tmp_path: Path) -> None:
    edges = _calls(tmp_path, {
        "src/a.ts": (
            "import { Util } from './util';\n"
            "import Def from './def';\n"
            "import * as ns from './ns';\n"
            "import * as lib from 'lodash';\n"
            "class Local { static make() {} }\n"
            "function run(v) {\n"
            "  Util.help(); Local.make(); ns.Tool.use(); ns.fn(); Def.go();\n"
            "  lib.chunk(); v.x(); lib.sub.y();\n"
            "  const k = new ns.Kit(); k.open();\n"
            "}\n"
        ),
        "src/util.ts": "export class Util { static help() {} }\n",
        "src/def.ts": "export default class D { static go() {} }\n",
        "src/ns.ts": "export class Tool { static use() {} }\nexport class Kit { open() {} }\nexport function fn() {}\n",
    }, "run")
    facts = {e.dst.split(":")[-2]: _facts(e) for e in edges if not e.is_resolved}
    assert facts["help"] == {"qualifier": "Util", "call_construct": "method"}
    assert facts["make"] == {"qualifier": "Local", "call_construct": "method"}
    assert facts["use"] == {"qualifier": "ns.Tool", "call_construct": "method"}
    assert facts["fn"] == {"qualifier": "ns", "call_construct": "method"}
    assert facts["go"] == {"qualifier": "Def", "call_construct": "method"}
    # A library's member, a variable's, a library's nested member: no qualifier.
    assert facts["chunk"] == {}
    assert facts["x"] == {"call_construct": "method"}
    assert facts["y"] == {"call_construct": "method"}
    # The type as written, through the namespace binding.
    assert facts["open"] == {"receiver_type_hint": "ns.Kit", "call_construct": "method"}


def test_new_binds_by_scope_then_by_the_table_then_as_before(tmp_path: Path) -> None:
    result = analyze_javascript(_write(tmp_path, {
        "src/a.js": (
            "import { K as Alias } from './k';\n"
            "import * as ns from './k';\n"
            "class Own {}\n"
            "function run() { new Own(); new Alias(); new ns.K(); new Lone(); new Map(); }\n"
        ),
        "src/k.js": "export class K {}\n",
        "src/z.js": "export class K {}\nexport class Own {}\nexport class Lone {}\n",
    }))
    by_id = {s.id: s for s in result.symbols}
    made = sorted(
        (by_id[e.dst].name, by_id[e.dst].path.rsplit("/", 1)[-1], e.meta["resolved_by"])
        for e in result.edges if e.edge_type == "instantiates" and e.dst in by_id
    )
    assert made == [
        ("K", "k.js", "import"), ("K", "k.js", "import"),
        ("Lone", "z.js", "name"),  # no stub path for ``new``: the class lookup stays
        ("Own", "a.js", "lexical_scope"),
    ]


def test_a_workspace_subpath_still_names_its_file_for_value_references(tmp_path: Path) -> None:
    """``_disambiguate_by_import`` now serves value references only (ADR-0064
    moved calls to the table); its workspace-subpath case is pinned here."""
    from hypergumbo_lang_mainstream.js_ts import _disambiguate_by_import

    result = analyze_javascript(_write(tmp_path, {
        "package.json": '{"name": "ws", "private": true, "workspaces": ["packages/*"]}',
        "packages/b/package.json": '{"name": "@ws/b", "version": "1.0.0"}',
        "packages/b/util.js": "function tool() {}\nmodule.exports = { tool };\n",
        "packages/b/other.js": "function tool() {}\nmodule.exports = { tool };\n",
        "packages/a/package.json": '{"name": "@ws/a", "version": "1.0.0"}',
        "packages/a/index.js": "function run() {}\n",
    }))
    tools = [s for s in result.symbols if s.name == "tool"]
    found = _disambiguate_by_import(
        "@ws/b/util", tmp_path / "packages/a/index.js", "tool", {"tool": tools},
    )
    assert found is not None and found.path.endswith("packages/b/util.js")


# ---------------------------------------------- through the shared pass


def _map(tmp_path: Path, files: dict[str, str]) -> dict[str, Any]:
    repo = _write(tmp_path / "repo", files)
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    return json.loads(out.read_text())


def _resolved(data: dict[str, Any], caller: str, callee: str) -> list[tuple[str, str, str]]:
    """(target's path, target's name, mark) of each live resolved call from
    ``caller`` to a symbol named ``callee`` or ``<Owner>.callee``."""
    nodes = {n["id"]: n for n in data["nodes"]}
    return sorted(
        (nodes[e["dst"]]["path"], nodes[e["dst"]]["name"], e["meta"]["resolved_by"])
        for e in data["edges"]
        if e["type"] == "calls" and e.get("is_resolved")
        and not e["meta"].get("superseded_by")
        and nodes.get(e["src"], {}).get("name") == caller and e["dst"] in nodes
        and nodes[e["dst"]]["name"].rsplit(".", 1)[-1] == callee
    )


_NAMESAKES = {
    "src/db.ts": "export class Conn { open() {} }\nexport function helper() {}\n",
    "legacy/db.ts": "export class Conn { open() {} }\nexport function helper() {}\n",
}


def test_end_to_end_a_typed_receiver_reaches_its_type_not_a_namesake(tmp_path: Path) -> None:
    data = _map(tmp_path, {**_NAMESAKES, "src/main.ts": (
        "import { Conn } from './db';\n"
        "export function run(c: Conn) { c.open(); }\n"
    )})
    assert _resolved(data, "run", "open") == [("src/db.ts", "Conn.open", "receiver_type")]


def test_end_to_end_an_imported_function_reaches_the_file_it_names(tmp_path: Path) -> None:
    data = _map(tmp_path, {**_NAMESAKES, "src/main.ts": (
        "import { helper as h } from './db';\n"
        "export function run() { h(); }\n"
    )})
    assert _resolved(data, "run", "helper") == [("src/db.ts", "helper", "import")]


def test_end_to_end_a_call_through_a_namespace_binding(tmp_path: Path) -> None:
    data = _map(tmp_path, {**_NAMESAKES, "src/main.ts": (
        "import * as db from '../legacy/db';\n"
        "export function run() { db.helper(); const c = new db.Conn(); c.open(); }\n"
    )})
    assert _resolved(data, "run", "helper") == [("legacy/db.ts", "helper", "import")]
    assert _resolved(data, "run", "open") == [("legacy/db.ts", "Conn.open", "receiver_type")]


def test_end_to_end_an_untyped_receiver_stays_unresolved(tmp_path: Path) -> None:
    data = _map(tmp_path, {**_NAMESAKES, "src/main.ts": (
        "export function run(c) { c.open(); }\n"
    )})
    assert _resolved(data, "run", "open") == []


def test_end_to_end_an_untyped_receiver_reaches_the_one_holder_of_the_name(tmp_path: Path) -> None:
    """ADR-0064, the name step: with ONE method of the name in the repository
    the call is bound and says it was bound by name. A name the language's own
    library defines on its types (``push``) is not: the library is a second
    holder, and the stub says so."""
    files = {
        "src/queue.ts": "export class Queue { drain() {} push(x) {} }\n",
        "src/main.ts": "export function run(c, items) { c.drain(); items.push(1); }\n",
    }
    data = _map(tmp_path, files)
    assert _resolved(data, "run", "drain") == [("src/queue.ts", "Queue.drain", "name")]
    assert _resolved(data, "run", "push") == []
    edges = _calls(tmp_path / "facts", files, "run")
    assert (_stub(edges, "push").meta or {}).get("stdlib_member_name") is True
    assert "stdlib_member_name" not in (_stub(edges, "drain").meta or {})


def test_end_to_end_inherited_this_and_super_calls(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "src/base.ts": "export class Base { ping() {} save() {} }\n",
        "src/stray.ts": "export class Base { ping() {} save() {} }\n",
        "src/child.ts": (
            "import { Base } from './base';\n"
            "export class Child extends Base {\n"
            "  save() { super.save(); this.ping(); }\n"
            "}\n"
        ),
    })
    assert _resolved(data, "Child.save", "ping") == [("src/base.ts", "Base.ping", "own_type")]
    assert _resolved(data, "Child.save", "save") == [("src/base.ts", "Base.save", "own_type")]


def test_end_to_end_a_class_qualifier_and_a_default_import(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "src/util.ts": "export class Util { static help() {} }\n",
        "lib/util.ts": "export class Util { static help() {} }\n",
        "src/fmt.ts": "export default function format() {}\n",
        "lib/fmt.ts": "export default function format() {}\n",
        "src/main.ts": (
            "import { Util } from './util';\n"
            "import fmt from './fmt';\n"
            "export function run() { Util.help(); fmt(); }\n"
        ),
    })
    assert _resolved(data, "run", "help") == [("src/util.ts", "Util.help", "qualified")]
    assert _resolved(data, "run", "format") == [("src/fmt.ts", "format", "import")]


_BARREL = {
    "src/lib/impl.ts": "export function helper() {}\nexport class Conn { open() {} }\n",
    "src/lib/index.ts": "export * from './impl';\nexport { helper as aid } from './impl';\n",
    # Namesakes a bind by name would reach, or tie with.
    "legacy/impl.ts": (
        "export function helper() {}\nexport function aid() {}\n"
        "export class Conn { open() {} }\n"
    ),
}


def test_end_to_end_names_imported_through_a_barrel_reach_their_declarations(
    tmp_path: Path,
) -> None:
    data = _map(tmp_path, {**_BARREL, "src/main.ts": (
        "import { helper, aid, Conn } from './lib';\n"
        "export function viaStar(c: Conn) { helper(); c.open(); new Conn(); }\n"
        "export function viaAlias() { aid(); }\n"
    )})
    assert _resolved(data, "viaStar", "helper") == [("src/lib/impl.ts", "helper", "import")]
    assert _resolved(data, "viaStar", "open") == [("src/lib/impl.ts", "Conn.open", "receiver_type")]
    assert _resolved(data, "viaAlias", "helper") == [("src/lib/impl.ts", "helper", "import")]
    assert _resolved(data, "viaAlias", "aid") == []
    nodes = {n["id"]: n for n in data["nodes"]}
    made = [
        (nodes[e["dst"]]["path"], e["meta"]["resolved_by"]) for e in data["edges"]
        if e["type"] == "instantiates" and nodes.get(e["src"], {}).get("name") == "viaStar"
    ]
    assert made == [("src/lib/impl.ts", "import")]


def test_end_to_end_a_commonjs_class_through_require(tmp_path: Path) -> None:
    data = _map(tmp_path, {
        "lib/client.js": "class Client { run() {} static make() {} }\nmodule.exports = Client;\n",
        "lib/other.js": "class Client { run() {} static make() {} }\nmodule.exports = Client;\n",
        "lib/helpers.js": "function helper() {}\nmodule.exports = { aid: helper };\n",
        "lib/more.js": "function aid() {}\nfunction helper() {}\nmodule.exports = { aid, helper };\n",
        "lib/main.js": (
            "const Client = require('./client');\n"
            "const { aid } = require('./helpers');\n"
            "function go() { const c = new Client(); c.run(); Client.make(); }\n"
            "function help() { aid(); }\n"
        ),
    })
    assert _resolved(data, "go", "run") == [("lib/client.js", "Client.run", "receiver_type")]
    assert _resolved(data, "go", "make") == [("lib/client.js", "Client.make", "qualified")]
    assert _resolved(data, "help", "helper") == [("lib/helpers.js", "helper", "import")]
