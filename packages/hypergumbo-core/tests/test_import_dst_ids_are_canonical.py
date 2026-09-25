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

from hypergumbo_core.cli import run_survey

# css is fixed at the analyzer (its own test pins the shape) but is NOT driven
# here: its ``imports`` edges never reach the survey at all -- their src is a
# hash-shaped symbol id (``css:sha256:...``, 3 slots) that the pipeline drops,
# so the edges are orphaned -- and every css symbol id trips id_format on its
# own. Both are a separate css limb of INV-dulah, filed; driving css here
# would make the non-vacuity guard fail for a reason this fix does not own.
CASES: dict[str, tuple[str, str]] = {
    "objc": ("App.m", '#import <Foundation/Foundation.h>\n#import "App.h"\n@implementation App\n@end\n'),
    "solidity": ("Token.sol", 'pragma solidity ^0.8.0;\nimport "./ERC20.sol";\ncontract Token {}\n'),
}


@pytest.fixture(scope="module")
def maps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict]:
    base = tmp_path_factory.mktemp("import-dst")
    out: dict[str, dict] = {}
    for lang, (filename, source) in CASES.items():
        repo = base / lang
        repo.mkdir()
        (repo / filename).write_text(source)
        target = base / f"{lang}.json"
        run_survey(repo, out_path=target, progress=False, include_docs=True)
        out[lang] = json.loads(target.read_text())
    return out


@pytest.mark.parametrize("lang", sorted(CASES))
def test_the_import_edges_are_there(lang: str, maps: dict[str, dict]) -> None:
    """Non-vacuity guard: each case reaches finalize with its import edges."""
    imports = [e for e in maps[lang]["edges"] if e.get("type") == "imports"]
    assert len(imports) >= 1, lang


@pytest.mark.parametrize("lang", sorted(CASES))
def test_no_node_carries_the_unknown_path_sentinel(lang: str, maps: dict[str, dict]) -> None:
    """The 4-part fallback never fired: no boundary node has ``<unknown>`` in
    its path slot, and every import dst names ``lang`` in its lang slot."""
    bad = [n["id"] for n in maps[lang]["nodes"] if ":<unknown>:" in n["id"]]
    assert bad == [], bad
    for e in maps[lang]["edges"]:
        if e.get("type") == "imports":
            assert e["dst"].split(":")[0] == lang, e["dst"]


@pytest.mark.parametrize("lang", sorted(CASES))
def test_the_validator_reports_no_id_format_violation(lang: str, maps: dict[str, dict]) -> None:
    report = maps[lang].get("validation_report") or {}
    by_class = report.get("violations_by_class") or {}
    assert by_class.get("id_format", 0) == 0, report.get("violations")
