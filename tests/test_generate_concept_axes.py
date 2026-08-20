# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ``scripts/generate-concept-axes``.

Named to match the script basename (with hyphens normalized to
underscores) so ``top_level_test_map.py`` selects this file when the
script changes — per-PR smart-test runs these tests automatically.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate-concept-axes"


def _load(path: Path, name: str) -> Any:
    """Load a Python source file as a module regardless of extension."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


generator = _load(SCRIPT_PATH, "generate_concept_axes")


# --- render() output shape ---

def test_render_includes_top_header_and_known_axes():
    out = generator.render()
    assert out.startswith("<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->")
    assert "AUTO-GENERATED" in out
    assert "## `Edge.type` axes" in out
    assert "### `relationship` — ADR-0023 compliant" in out
    assert "### `endpoint_shape`" in out
    assert "### `pending_classification`" in out


def test_render_lists_canonical_relationship_values():
    """Every relationship-axis value must appear under its section."""
    from hypergumbo_core.edge_types import (
        AXIS_RELATIONSHIP, EDGE_TYPES,
    )

    out = generator.render()
    for spec in EDGE_TYPES:
        if spec.axis != AXIS_RELATIONSHIP:
            continue
        assert f"`{spec.name}`" in out
        assert spec.description in out


def test_render_groups_by_axis():
    """A relationship-shaped value must precede an endpoint-shape value
    in the rendered doc (i.e., the sections are in canonical order)."""
    out = generator.render()
    rel_idx = out.find("### `relationship`")
    end_idx = out.find("### `endpoint_shape`")
    pen_idx = out.find("### `pending_classification`")
    assert 0 < rel_idx < end_idx < pen_idx


def test_render_has_trailing_newline():
    out = generator.render()
    assert out.endswith("\n")
    assert not out.endswith("\n\n\n")  # no excessive trailing blanks


def test_format_axis_section_handles_empty_axis():
    out = generator._format_axis_section(
        "test heading", "preamble", [],
    )
    assert "_(empty —" in out


# --- WI-limom: the ADR-0039 base_confidence projection ---

def test_render_includes_confidence_projection_section():
    out = generator.render()
    assert "### Derived confidence — `base_confidence` projection" in out
    assert "| Derived confidence | Pathways |" in out


def test_every_seeded_pathway_appears_with_its_value():
    """The table is a faithful projection: every seeded pathway is listed
    under a row whose value is its registry `base_confidence`."""
    from hypergumbo_core.evidence_types import EVIDENCE_TYPES

    out = generator.render()
    table = out.split("### Derived confidence")[1]
    rows = {
        line.split("|")[1].strip().strip("*"): line
        for line in table.splitlines()
        if line.startswith("| **")
    }
    seeded = [e for e in EVIDENCE_TYPES if e.base_confidence is not None]
    assert seeded, "registry has no seeded pathways — projection is vacuous"
    for spec in seeded:
        key = f"{spec.base_confidence:.2f}"
        assert key in rows, f"no table row for confidence {key}"
        assert f"`{spec.name}`" in rows[key], (
            f"{spec.name} missing from the {key} row"
        )


def test_unseeded_pathways_are_disclosed_not_hidden():
    from hypergumbo_core.evidence_types import EVIDENCE_TYPES

    out = generator.render()
    unseeded = [e for e in EVIDENCE_TYPES if e.base_confidence is None]
    assert f"**Unseeded ({len(unseeded)}).**" in out
    for spec in unseeded:
        assert f"`{spec.name}`" in out


def test_is_resolved_conditioned_pathways_get_both_values():
    from hypergumbo_core.evidence_types import EVIDENCE_TYPES

    out = generator.render()
    assert "| Pathway | Resolved | Unresolved |" in out
    for spec in EVIDENCE_TYPES:
        if spec.base_confidence_unresolved is None:
            continue
        assert (
            f"| `{spec.name}` | {spec.base_confidence:.2f} "
            f"| {spec.base_confidence_unresolved:.2f} |"
        ) in out


def test_confidence_annotation_renders_inline_on_evidence_bullets():
    """Per-value lookup happens on the bullet, not just the summary table."""
    out = generator.render()
    assert "_(derived confidence 0.85; 0.40 when unresolved)_" in out
    assert "_(derived confidence 0.95)_" in out


def test_confidence_annotation_is_empty_for_registries_without_the_field():
    """Edge-type / symbol-kind specs carry no confidence axis; the shared
    bullet formatter must stay silent for them rather than inventing one."""
    from hypergumbo_core.edge_types import EDGE_TYPES
    from hypergumbo_core.symbol_kinds import SYMBOL_KINDS

    assert generator._confidence_annotation(EDGE_TYPES[0]) == ""
    assert generator._confidence_annotation(SYMBOL_KINDS[0]) == ""


def test_confidence_annotation_is_empty_for_unseeded_pathway():
    from hypergumbo_core.evidence_types import EVIDENCE_TYPES

    unseeded = next(e for e in EVIDENCE_TYPES if e.base_confidence is None)
    assert generator._confidence_annotation(unseeded) == ""


# --- main() ---

def test_main_writes_doc_when_no_check_flag(tmp_path: Path, capsys):
    fake_root = tmp_path
    (fake_root / "docs").mkdir()
    with patch.object(generator, "REPO_ROOT", fake_root):
        rc = generator.main([])
    assert rc == 0
    out = (fake_root / "docs" / "concept-axes.md").read_text()
    assert "# Concept Axes" in out
    captured = capsys.readouterr()
    assert "Generated" in captured.out


def test_main_check_passes_on_fresh_doc(tmp_path: Path, capsys):
    fake_root = tmp_path
    (fake_root / "docs").mkdir()
    # First write the doc, then verify --check passes.
    with patch.object(generator, "REPO_ROOT", fake_root):
        generator.main([])
        rc = generator.main(["--check"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "up-to-date" in captured.out


def test_main_check_fails_when_doc_missing(tmp_path: Path, capsys):
    fake_root = tmp_path
    with patch.object(generator, "REPO_ROOT", fake_root):
        rc = generator.main(["--check"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "does not exist" in captured.err


def test_main_check_fails_when_doc_stale(tmp_path: Path, capsys):
    fake_root = tmp_path
    (fake_root / "docs").mkdir()
    (fake_root / "docs" / "concept-axes.md").write_text("# stale content\n")
    with patch.object(generator, "REPO_ROOT", fake_root):
        rc = generator.main(["--check"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "out of date" in captured.err


def test_main_creates_docs_dir_if_missing(tmp_path: Path):
    fake_root = tmp_path  # docs/ doesn't exist yet
    with patch.object(generator, "REPO_ROOT", fake_root):
        rc = generator.main([])
    assert rc == 0
    assert (fake_root / "docs" / "concept-axes.md").exists()


# --- committed doc smoke test ---

def test_committed_doc_matches_render():
    """The doc on disk must exactly equal what the generator produces.

    This is the same invariant the pre-commit ``--check`` enforces;
    repeating it as a pytest case so CI catches the same drift even
    if the pre-commit hook is bypassed.
    """
    on_disk = (REPO_ROOT / "docs" / "concept-axes.md").read_text()
    rendered = generator.render()
    assert on_disk == rendered, (
        "docs/concept-axes.md is out of date. "
        "Regenerate with: ./scripts/generate-concept-axes"
    )
