# SPDX-License-Identifier: AGPL-3.0-or-later
"""One ``require('node:path')``, one module spelling on every edge (WI-fiham).

The javascript analyzer strips the ``node:`` scheme from a CALL's module slot
(``_normalize_import_module_hint``: "node:fs -> fs, the Node 16+ canonical form
for built-ins") but emitted the raw specifier on the IMPORT edge and on the
``module_attr_ref`` edges read off the same binding. Measured on express:
``node:path`` 32 import edges + 11 attribute reads, ``node:http`` 6 + 2,
``node:querystring`` 1 + 1, and zero call edges spelled that way.

WHY IT WITHHELD VERDICTS. ``verify-claims``' uncatalogued-module gate walks
``module_attr_ref`` edges (the item filed this as the import edge; the gate's
``_CALL_SITE_EDGE_TYPES`` excludes ``imports``, so the edges that reached it
were the attribute reads) and asks ``module_io_is_enumerated``, which is EXACT
by design (INV-buzab / INV-zubuh). ``path`` carries a dated completeness grant;
``node:path`` cannot. So express reported ``node:http``, ``node:path`` and
``node:querystring`` as modules "the I/O catalog could not classify" -- a
spelling of modules whose calls it classifies.

WHY STRIPPING IS EXACT. A ``node:`` URL loads a Node built-in and nothing else
(Node docs, "Modules: node: imports"), and every bare spelling the shipped
catalogue grants (``path``, ``url``, ``zlib``, ``crypto``, ``fs``, ``process``,
...) is a core module, which ``require`` returns ahead of any ``node_modules``
package of the same name. So ``node:path`` and ``path`` are one module and the
grant that covers one covers the other.

WHAT IS LEFT ALONE. Only the URL-style SCHEME is stripped (``node:``, and the
``npm:`` / ``jsr:`` / ``http(s)://`` prefixes whose colons broke the five-slot
id the same way). The relative leaders ``./`` / ``../`` are NOT stripped on
these edges: ``is_definitionally_first_party`` reads them to know a module is
the repository's own (INV-juvul), and stripping them would turn ``./http`` into
node's ``http``.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.ir import symbol_path_slot
from hypergumbo_lang_mainstream.js_ts import analyze_javascript

CJS = """const path = require('node:path');
const qs = require('node:querystring');
const post = require('./post');

function run(p) {
  const sep = path.sep;
  const joined = path.join(p, sep);
  const parsed = qs.parse(joined);
  return post.name + parsed;
}
module.exports = { run };
"""

ESM = """import * as fs from 'node:fs';

export function mode() {
  return fs.constants.R_OK;
}
"""


def _slots(edges, edge_type: str) -> set[str]:
    return {symbol_path_slot(e.dst) for e in edges if e.edge_type == edge_type}


def test_the_import_edge_drops_the_scheme(tmp_path: Path) -> None:
    (tmp_path / "a.js").write_text(CJS)
    imports = _slots(analyze_javascript(tmp_path).edges, "imports")
    assert {"path", "querystring"} <= imports, imports
    assert not {m for m in imports if m.startswith("node:")}, imports


def test_the_attribute_read_drops_the_scheme(tmp_path: Path) -> None:
    (tmp_path / "a.js").write_text(CJS)
    edges = analyze_javascript(tmp_path).edges
    reads = [e for e in edges if e.edge_type == "module_attr_ref"]
    sep = [e for e in reads if e.dst.endswith(":path.sep:external_symbol")
           or ":path.sep:" in e.dst]
    assert sep, [e.dst for e in reads]
    assert {symbol_path_slot(e.dst) for e in sep} == {"path"}
    assert all(e.dst_ref is None or e.dst_ref.module_path == "path" for e in sep)
    assert not {m for m in _slots(edges, "module_attr_ref") if m.startswith("node:")}


def test_import_attribute_and_call_agree(tmp_path: Path) -> None:
    """THE INVARIANT the item states: one require, one spelling."""
    (tmp_path / "a.js").write_text(CJS)
    edges = analyze_javascript(tmp_path).edges
    call = [e for e in edges if e.edge_type == "calls" and ":join:" in e.dst]
    assert call, [e.dst for e in edges if e.edge_type == "calls"]
    assert {symbol_path_slot(e.dst) for e in call} == {"path"}
    assert "path" in _slots(edges, "imports")
    assert "path" in _slots(edges, "module_attr_ref")


def test_esm_namespace_import_too(tmp_path: Path) -> None:
    (tmp_path / "m.js").write_text(ESM)
    edges = analyze_javascript(tmp_path).edges
    assert "fs" in _slots(edges, "imports")
    assert "fs" in _slots(edges, "module_attr_ref")
    assert not {m for e_t in ("imports", "module_attr_ref")
                for m in _slots(edges, e_t) if m.startswith("node:")}


def test_a_relative_specifier_keeps_its_leader(tmp_path: Path) -> None:
    """CONTROL: the first-party evidence INV-juvul reads is not erased."""
    (tmp_path / "a.js").write_text(CJS)
    edges = analyze_javascript(tmp_path).edges
    assert "./post" in _slots(edges, "imports")
    assert "./post" in _slots(edges, "module_attr_ref")


def test_the_gate_no_longer_withholds_on_a_scheme_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through the shipped CLI and the shipped catalogue. Before
    this change the only blocker was the attribute read's spelling: "calls
    into 1 module(s) that the I/O catalog could not classify (node:path)",
    ``inconclusive``. ``path`` carries a dated completeness grant ("String
    arithmetic on paths"), and ``node:path`` IS ``path``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.js").write_text(
        "const path = require('node:path');\n"
        "function run() {\n  return path.sep;\n}\nmodule.exports = { run };\n")
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: NO-FSWRITE\n    text: t\n    constraint:\n"
        "      boundary: fs_write\n      must_not_exist: true\n")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    assert "node:" not in verdict["details"], verdict["details"]
    assert verdict["verdict"] == "confirmed", verdict


def test_an_ungranted_module_still_withholds_under_its_bare_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONTROL: dropping the scheme is a respelling, not a grant.
    ``querystring`` has no completeness entry, so a read of it withholds."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.js").write_text(
        "const qs = require('node:querystring');\n"
        "function run() {\n  return qs.escape;\n}\nmodule.exports = { run };\n")
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: NO-FSWRITE\n    text: t\n    constraint:\n"
        "      boundary: fs_write\n      must_not_exist: true\n")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    assert verdict["verdict"] == "inconclusive", verdict
    assert "(querystring)" in verdict["details"], verdict["details"]
