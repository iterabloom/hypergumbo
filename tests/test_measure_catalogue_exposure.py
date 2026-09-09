# SPDX-License-Identifier: AGPL-3.0-or-later
"""The exposure census must not repeat the mistake it exists to correct.

INV-linub's 2026-08-06 exposure table ranked languages by METHOD-KIND SHARE
alone and concluded ``c/cpp/elixir/erlang/haskell: 0% method-kind -- NOT at
risk from this class``. WI-lajus falsified that for C a month later: 156 of 324
corpus socket sites classify as nothing, through ambiguous names carrying no
module slot from ``#include`` — a route needing no method-kind rows at all.

These tests pin the two properties that failure came from. A census that
reports only ``R1`` regresses to the falsified table; a census whose ``R2``
silently reads zero for a catalogue that declares no ``ambiguous_names`` hides
the same gap behind a passing number.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "measure-catalogue-exposure.py"
)


def _load() -> ModuleType:
    """Load the SHIPPED script, not a copy of its logic.

    The filename carries hyphens, so it is not importable by name. Copying the
    logic into the test would let the two drift, which is the whole failure
    mode this instrument exists to catch elsewhere.
    """
    spec = importlib.util.spec_from_loader(
        "measure_catalogue_exposure",
        importlib.machinery.SourceFileLoader(
            "measure_catalogue_exposure", str(SCRIPT)),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # ``@dataclass`` resolves its own module through ``sys.modules`` while the
    # class body executes, so registering AFTER exec_module raises
    # ``AttributeError: 'NoneType' object has no attribute '__dict__'``.
    sys.modules["measure_catalogue_exposure"] = module
    spec.loader.exec_module(module)
    return module


measure_catalogue_exposure = _load()


class TestTheFalsifiedTableCannotComeBack:
    def test_c_is_reported_at_risk_despite_zero_method_kind_rows(self) -> None:
        """The exact cell the old table got wrong."""
        row = measure_catalogue_exposure.exposure("c")
        assert row.r1_method == 0, "premise moved: C now has method-kind rows"
        assert row.r2_ambiguous > 0
        assert row.at_risk > 0, "C must not read as not-at-risk"

    def test_kotlin_reproduces_the_receiver_syntax_exposure(self) -> None:
        """The old table's own control: kotlin at 95% method-kind."""
        row = measure_catalogue_exposure.exposure("kotlin")
        assert row.r1_method / row.total > 0.8

    @pytest.mark.parametrize("lang", ["cpp", "elixir", "erlang", "haskell"])
    def test_every_dismissed_language_is_at_risk(self, lang: str) -> None:
        row = measure_catalogue_exposure.exposure(lang)
        assert row.r1_method == 0
        assert row.r2_ambiguous > 0


class TestAZeroAndAMissingInputDoNotLookTheSame:
    def test_a_catalogue_declaring_no_ambiguous_names_says_so(self) -> None:
        """``ambiguous_declared`` distinguishes 'none' from 'never stated'."""
        rows = {r.language: r for r in measure_catalogue_exposure.census()}
        assert any(r.ambiguous_declared for r in rows.values())
        for row in rows.values():
            if not row.ambiguous_declared:
                assert row.r2_ambiguous == 0

    def test_an_unknown_language_raises_rather_than_scoring_zero(self) -> None:
        with pytest.raises(ValueError, match="no shipped catalogue"):
            measure_catalogue_exposure.exposure("not-a-language")


class TestBothCountsAreNamed:
    def test_row_and_distinct_counts_are_reported_separately(self) -> None:
        """One fact, one home: the dedup discrepancy is disclosed, not hidden.

        The lab-notebook prototype counted C's R2 as 16 in one script and 22 in
        another — deduped by ``qualified_name`` vs raw rows. Both are true of
        different things. Reporting only one recreates the second silent home.
        """
        row = measure_catalogue_exposure.exposure("c")
        assert row.r2_ambiguous >= row.r2_distinct
        assert row.r2_distinct > 0


class TestTheCensusIsCompleteAndRunnable:
    def test_census_covers_every_shipped_catalogue(self) -> None:
        langs = {r.language for r in measure_catalogue_exposure.census()}
        shipped = {
            p.stem
            for p in measure_catalogue_exposure.catalogue_dir().glob("*.yaml")
        }
        assert langs == shipped

    def test_cli_emits_json_for_every_language(self, tmp_path: Path) -> None:
        out = tmp_path / "census.json"
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--json", str(out)],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, proc.stderr
        data = json.loads(out.read_text())
        assert len(data) > 10
        assert {"language", "r1_method", "r2_ambiguous"} <= set(data[0])

    def test_text_table_names_both_routes(self) -> None:
        text = measure_catalogue_exposure.render(
            measure_catalogue_exposure.census()
        )
        assert "R1" in text and "R2" in text
        assert "c " in text or "\nc" in text
