# SPDX-License-Identifier: AGPL-3.0-or-later
"""No module the classifier matched a call into is reported starved (WI-bobuf).

INV-dihun's statement is general, and it was fixed one row at a time, each
instance found by its symptom: INV-nular (rust ``File::open``), WI-komun (JS
``console``), INV-fugus (python ``datetime``), INV-dihun (JS ``process``,
python ``ctypes.cdll``), WI-zohuk / WI-dapap (a JS handler assignment, a python
protocol edge). This asks the question of the OUTPUT, over one fixture per
known instance and the schema-coverage corpus, through
:func:`verify_claims.classified_yet_starved`.

Two live shapes are held as strict xfails naming the item that owns their
cure (WI-momiz): an unbound / UFCS call with no construct stamp into a method-only module
(Rust ``std::io::Write::write_all(out, b)``, python
``csv.DictWriter.writerow(w, row)``), which ADR-0059's own re-open trigger
(a row reached both ways) names.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.verify_claims import classified_yet_starved, method_starved_modules

_CORPUS = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "schema-coverage-corpus"

_INSTANCES: dict[str, dict[str, str]] = {
    "rust_file_open": {
        "Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\n',
        "src/main.rs": 'use std::fs::File;\nuse std::io::Read;\n\nfn main() {\n'
                       '    let mut f = File::open("x").unwrap();\n    let mut s = String::new();\n'
                       '    f.read_to_string(&mut s).unwrap();\n}\n',
    },
    "js_console_process": {
        "app.js": "function f() {\n  console.log(process.env.X);\n  process.exit(1);\n}\nmodule.exports = { f };\n",
    },
    "js_handler_assignment": {
        "app.js": ("const ws = new WebSocket('ws://x');\nws.onmessage = function (ev) { console.log(ev.data); };\n"
                   "sink.write('x');\n"),
    },
    "python_datetime_ctypes": {
        "app.py": ("import ctypes\nimport datetime\n\n\ndef f():\n    lib = ctypes.cdll.LoadLibrary('x')\n"
                   "    return datetime.datetime.now(), lib\n"),
    },
}

_UFCS: dict[str, dict[str, str]] = {
    "rust_ufcs": {
        "Cargo.toml": '[package]\nname = "a"\nversion = "0.1.0"\n',
        # ``out.flush()`` is a construct-stamped edge, so rust is not abstained on.
        "src/main.rs": 'use std::io::Write;\n\nfn main() {\n    let mut out = std::io::stdout();\n'
                       '    std::io::Write::write_all(&mut out, b"hello\\n").unwrap();\n'
                       '    out.flush().unwrap();\n}\n',
    },
    "python_unbound": {
        # ``q.exists()`` is the construct-stamped edge elsewhere in the language.
        "app.py": ("import csv\nimport pathlib\n\n\ndef f(row, w):\n    q = pathlib.Path('x')\n"
                   "    q.exists()\n    csv.DictWriter.writerow(w, row)\n"),
    },
}


def _contradiction(tmp_path: Path, files: dict[str, str] | None = None, repo: Path | None = None) -> list[str]:
    if repo is None:
        repo = tmp_path / "repo"
        for rel, text in (files or {}).items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=repo, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    languages = {n["language"] for n in data["nodes"] if n.get("language")}
    catalogs = {}
    for lang in sorted(languages):
        try:
            catalogs[lang] = load_catalog(lang)
        except (FileNotFoundError, KeyError, ValueError):
            continue
    assert catalogs  # reach
    return classified_yet_starved(data["edges"], catalogs)


def test_the_check_reports_the_contradiction() -> None:
    """A starved module with a classified edge into it is reported."""
    edges = [{"type": "calls", "src": "rust:src/main.rs:1-2:main:function",
              "dst": "rust:std::fs::File:0-0:read_to_string:external_symbol", "meta": {}},
             {"type": "calls", "src": "rust:src/main.rs:1-2:main:function",
              "dst": "rust:x:0-0:y:external_symbol", "meta": {"call_construct": "method"}}]
    catalogs = {"rust": load_catalog("rust")}
    assert "std::fs::File" in method_starved_modules(edges, catalogs)  # reach: it starves
    assert classified_yet_starved(edges, catalogs) == ["std::fs::File"]


@pytest.mark.parametrize("name", sorted(_INSTANCES))
def test_no_known_instance_recurs(tmp_path: Path, name: str) -> None:
    assert _contradiction(tmp_path, _INSTANCES[name]) == []


def test_the_schema_coverage_corpus_has_no_contradiction(tmp_path: Path) -> None:
    assert _contradiction(tmp_path, repo=_CORPUS) == []


@pytest.mark.parametrize("name", [
    pytest.param(name, marks=pytest.mark.xfail(strict=True, reason=(
        "WI-momiz: an unbound / UFCS call with no construct stamp into a "
        "method-only module is classified and reported starved")))
    for name in sorted(_UFCS)
])
def test_an_unbound_call_is_not_starved(tmp_path: Path, name: str) -> None:
    assert _contradiction(tmp_path, _UFCS[name]) == []
