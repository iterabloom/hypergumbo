# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-midag's cross-language gate for hypergumbo-lang-common: every analyzer that finds a
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

import importlib
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "hypergumbo_lang_common"
_LOOKUP = re.compile(r"^def _(?:get|find)_enclosing_(?:function|method|callable)\w*\(", re.M)

#: module -> ("verified", "test_module::test_name") | ("unverified", row id)
CLASSIFIED: dict[str, tuple[str, str]] = {
    "elixir": ("verified", "test_elixir_enclosing_anchor::test_same_short_name_in_two_modules_of_one_file"),
    "erlang": ("verified", "test_erlang_enclosing_anchor::test_ifdef_alternatives_anchor_to_the_branch_that_contains_the_call"),
    "haskell": ("verified", "test_haskell_equation_span::test_every_call_is_inside_its_src"),
    "dart": ("unverified", "WI-mapor"),
    "elm": ("unverified", "WI-mapor"),
    "fsharp": ("unverified", "WI-mapor"),
    "glsl": ("unverified", "WI-mapor"),
    "hlsl": ("unverified", "WI-mapor"),
    "julia": ("unverified", "WI-mapor"),
    "matlab": ("unverified", "WI-mapor"),
    "nix": ("unverified", "WI-mapor"),
    "r_lang": ("unverified", "WI-mapor"),
    "scheme": ("unverified", "WI-mapor"),
    "starlark": ("unverified", "WI-mapor"),
    "wgsl": ("unverified", "WI-mapor"),
}


def _modules_with_a_lookup() -> set[str]:
    return {p.stem for p in _SRC.glob("*.py") if _LOOKUP.search(p.read_text(encoding="utf-8"))}


def test_every_enclosing_lookup_is_classified() -> None:
    found = _modules_with_a_lookup()
    assert "elixir" in found, "reach: the scan must see a known lookup"
    assert found == set(CLASSIFIED), (
        f"unclassified: {sorted(found - set(CLASSIFIED))}; "
        f"stale: {sorted(set(CLASSIFIED) - found)}")


@pytest.mark.parametrize("module", sorted(m for m, (s, _) in CLASSIFIED.items() if s == "verified"))
def test_a_verified_entry_names_a_real_test(module: str) -> None:
    test_module, test_name = CLASSIFIED[module][1].split("::")
    assert callable(getattr(importlib.import_module(test_module), test_name, None)), CLASSIFIED[module]
