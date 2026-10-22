# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-dulah, lang-slot limb: an import edge's dst is a canonical id all the
way through the production pipeline.

Three analyzers emitted an ``imports`` edge whose dst was the BARE import
path -- objc (``#import``), css (``@import``), solidity (``import``). A bare
path is not an id: finalize's 4-part fallback (``ir.py``, the
``<unknown>`` path sentinel) rendered it with the PATH in the lang slot,

    Foundation/Foundation.h:<unknown>:0-0:Foundation/Foundation.h:external_symbol

and the validator flagged every such node as ``non_canonical_language_prefix``
-- the entire id_format residual on Mantle (44 of 44, INV-dulah 2026-08-24).

This is the PRODUCTION path (``run_survey``, the CLI's own driver), not the
analyzers in isolation: the analyzers' own tests pin the emitted shape, this
one pins that the shape survives finalize and satisfies the validator, which
is the behavioural claim the invariant makes. The non-vacuity guard comes
first, because a producer that stopped emitting import edges would turn the
two properties below green by emptying them.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.catalog import all_known_languages
from hypergumbo_core.cli import run_survey

KNOWN_LANGUAGES = frozenset(all_known_languages())

# css is fixed at the analyzer (its own test pins the shape) but is NOT driven
# here: its ``imports`` edges never reach the survey at all -- their src is a
# hash-shaped symbol id (``css:sha256:...``, 3 slots) that the pipeline drops,
# so the edges are orphaned -- and every css symbol id trips id_format on its
# own. Both are a separate css limb of INV-dulah, filed; driving css here
# would make the non-vacuity guard fail for a reason this fix does not own.
#: ``case -> ({filename: source}, the edge type whose dst is checked)``.
#: WI-bufar-pokam added the five producers the fleet count found after the
#: objc/solidity fix (json_config, make, jsonnet, astro, svelte), plus Cargo
#: targets in toml_config, which carried the same bare-path dst.
CASES: dict[str, tuple[dict[str, str], str]] = {
    "objc": ({"App.m": '#import <Foundation/Foundation.h>\n#import "App.h"\n@implementation App\n@end\n'}, "imports"),
    "solidity": ({"Token.sol": 'pragma solidity ^0.8.0;\nimport "./ERC20.sol";\ncontract Token {}\n'}, "imports"),
    "json": ({"package.json": '{"name": "p", "main": "./dist/index.js", "bin": {"p": "./bin/cli.js"}}\n'}, "defines_target"),
    "makefile": ({"Makefile": "all: build test\n\nbuild: main.c\n\tcc main.c\n"}, "depends_on"),
    "jsonnet": ({"alerts.jsonnet": "local lib = import 'lib/util.libsonnet';\n{ a: lib }\n"}, "imports"),
    "astro": ({"Page.astro": "---\nimport Header from './Header.astro';\n---\n<Header />\n"}, "imports"),
    "svelte": ({"App.svelte": "<script>\nimport Button from './Button.svelte';\n</script>\n<Button />\n"}, "imports"),
    "toml": ({"Cargo.toml": '[package]\nname = "p"\n\n[[bin]]\nname = "p"\npath = "src/bin/p.rs"\n'}, "defines_target"),
}


@pytest.fixture(scope="module")
def maps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    base = tmp_path_factory.mktemp("import-dst")
    out: dict[str, dict] = {}
    for lang, (files, _edge_type) in CASES.items():
        repo = base / lang
        repo.mkdir()
        for filename, source in files.items():
            (repo / filename).write_text(source)
        target = base / f"{lang}.json"
        run_survey(repo, out_path=target, progress=False, include_docs=True)
        out[lang] = json.loads(target.read_text())
    return out


@pytest.mark.parametrize("lang", sorted(CASES))
def test_the_import_edges_are_there(lang: str, maps: dict[str, dict]) -> None:
    """Non-vacuity guard: each case reaches finalize with its edges."""
    edge_type = CASES[lang][1]
    edges = [e for e in maps[lang]["edges"] if e.get("type") == edge_type]
    assert len(edges) >= 1, lang


@pytest.mark.parametrize("lang", sorted(CASES))
def test_no_node_carries_the_unknown_path_sentinel(lang: str, maps: dict[str, dict]) -> None:
    """The 4-part fallback never fired: no boundary node has ``<unknown>`` in
    its path slot, and every checked dst has five slots and a canonical
    language in its lang slot."""
    bad = [n["id"] for n in maps[lang]["nodes"] if ":<unknown>:" in n["id"]]
    assert bad == [], bad
    edge_type = CASES[lang][1]
    for e in maps[lang]["edges"]:
        if e.get("type") == edge_type:
            dst = e["dst"]
            assert dst.count(":") >= 4, dst
            # A canonical language in the lang slot, never the path. (Not
            # always the case's own: the JS/TS analyzer also reads a
            # ``.svelte`` file's <script> and writes ``javascript:``.)
            assert dst.split(":")[0] in KNOWN_LANGUAGES, dst


@pytest.mark.parametrize("lang", sorted(CASES))
def test_the_validator_reports_no_id_format_violation(lang: str, maps: dict[str, dict]) -> None:
    report = maps[lang].get("validation_report") or {}
    by_class = report.get("violations_by_class") or {}
    assert by_class.get("id_format", 0) == 0, report.get("violations")
