# SPDX-License-Identifier: AGPL-3.0-or-later
"""An external dependency declaration carries directness and ecosystem (INV-fuhin).

ADR-0041 declares ``directness`` and ``ecosystem`` for the external
dependencies a project relates to, and a manifest declaration is the most
direct evidence of that relationship. Both were stamped only on boundary nodes,
so ``supply_chain_summary.external_dep`` counted every declaration under
``unknown`` for both keys. A declaration of an in-tree package is not external
(INV-kutub) and carries neither.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import run_behavior_map
from hypergumbo_core.supply_chain import declaration_ecosystem


@pytest.mark.parametrize("path, name, want", [
    ("composer.json", "php", "stdlib"),
    ("composer.json", "ext-json", "stdlib"),
    ("composer.json", "composer-plugin-api", "stdlib"),
    ("composer.json", "monolog/monolog", "third_party"),
    ("Cargo.toml", "serde", "third_party"),
    ("pyproject.toml", "php", "third_party"),  # only composer has platform packages
])
def test_declaration_ecosystem(path: str, name: str, want: str) -> None:
    assert declaration_ecosystem(f"sub/{path}", name) == want


def test_a_survey_stamps_external_declarations_and_not_in_tree_ones(tmp_path: Path) -> None:
    (tmp_path / "composer.json").write_text(
        '{"name": "a/b", "require": {"php": ">=8.1", "monolog/monolog": "^3"}}\n')
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "0"\ndependencies = ["requests", "sibling"]\n')
    (tmp_path / "sib").mkdir()
    (tmp_path / "sib" / "pyproject.toml").write_text('[project]\nname = "sibling"\nversion = "0"\n')
    out = tmp_path / "map.json"
    run_behavior_map(repo_root=tmp_path, out_path=out, include_sketch_precomputed=False)
    data = json.loads(out.read_text())
    stamps = {
        n["name"]: ((n.get("meta") or {}).get("directness"), (n.get("meta") or {}).get("ecosystem"))
        for n in data["nodes"] if n["kind"] == "dependency"
    }
    assert stamps == {
        "php": ("direct", "stdlib"),
        "monolog/monolog": ("direct", "third_party"),
        "requests": ("direct", "third_party"),
        "sibling": (None, None),
    }
