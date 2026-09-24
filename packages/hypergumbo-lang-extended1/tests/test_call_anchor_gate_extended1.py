# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag's cross-language gate for hypergumbo-lang-extended1: every analyzer that finds a
call's enclosing function is classified, so none can drift silently.

The same gate as hypergumbo-lang-mainstream's test_call_anchor_gate.py; see its
docstring for the statement and the mechanism. It enumerates every module in
this package that DEFINES an enclosing-function lookup. Each must be VERIFIED
(named containment test, which runs the analyzer on a same-named-declarations
fixture) or UNVERIFIED (the residual row). The UNVERIFIED half is bookkeeping,
not a check: it records that nobody has looked, so a new analyzer cannot arrive
unclassified and read as covered.
"""
from __future__ import annotations

import re
from pathlib import Path


_SRC = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_lang_extended1"
_LOOKUP = re.compile(r"^def _(?:get|find)_enclosing_(?:function|method|callable)\w*\(", re.M)

#: module -> ("verified", "test_module::test_name") | ("unverified", row id)
CLASSIFIED: dict[str, tuple[str, str]] = {
    "d_lang": ("unverified", "WI-mapor"),
    "fennel": ("unverified", "WI-mapor"),
    "fish": ("unverified", "WI-mapor"),
    "gdscript": ("unverified", "WI-mapor"),
    "gleam": ("unverified", "WI-mapor"),
    "hack": ("unverified", "WI-mapor"),
    "janet": ("unverified", "WI-mapor"),
    "llvm_ir": ("unverified", "WI-mapor"),
    "odin": ("unverified", "WI-mapor"),
    "pascal": ("unverified", "WI-mapor"),
    "solidity": ("unverified", "WI-mapor"),
    "v_lang": ("unverified", "WI-mapor"),
}


def _modules_with_a_lookup() -> set[str]:
    return {p.stem for p in _SRC.glob("*.py") if _LOOKUP.search(p.read_text(encoding="utf-8"))}


def test_every_enclosing_lookup_is_classified() -> None:
    found = _modules_with_a_lookup()
    assert "solidity" in found, "reach: the scan must see a known lookup"
    assert found == set(CLASSIFIED), (
        f"unclassified: {sorted(found - set(CLASSIFIED))}; "
        f"stale: {sorted(set(CLASSIFIED) - found)}")
