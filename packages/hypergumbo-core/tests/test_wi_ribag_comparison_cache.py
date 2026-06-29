# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-ribag: warm-sketch comparison sketches are read from cache, not regenerated.

The representativeness table compares the requested-budget sketch against 4x and
16x budget sketches. Those two extra `generate_sketch` calls dominated warm-cache
sketch wall-clock (~83% on the self-corpus: 155s total vs 27s with
`--no-comparison-sketches`) because, although their text was `cache_write`-ten to
the per-(repo, state, analyzer-identity) cache dir, nothing read it back — they
regenerated on every invocation (an INV-papuj cache-read miss).

The fix caches each comparison sketch's :class:`SketchStats` (the data the table
needs) alongside its already-cached text, and `_get_or_generate_comparison_sketch`
reads both back when present for the current state — skipping the expensive
regeneration while keeping the table always-on. A present file in the
state-scoped cache dir is fresh by construction (a source change rotates the
dir). See INV-papuj (umbrella) / WI-ribag.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypergumbo_core import cli
from hypergumbo_core.cli import (
    ConfigExtractionMode,
    _generate_sketch_filename,
    _get_or_generate_comparison_sketch,
)
from hypergumbo_core.sketch import SketchStats


# --------------------------------------------------------------------------
# SketchStats (de)serialization for the on-disk stats cache
# --------------------------------------------------------------------------


class TestSketchStatsSerialization:
    def test_round_trips_through_dict(self) -> None:
        s = SketchStats(
            token_budget=32000, total_in_degree=120,
            key_symbols_in_degree=42, has_key_symbols=True,
            entrypoints_confidence=3.5, has_entrypoints=True,
        )
        reloaded = SketchStats.from_dict(json.loads(json.dumps(SketchStats.to_dict(s))))
        assert reloaded == s

    def test_from_dict_ignores_unknown_keys(self) -> None:
        # Forward-compat: a stats cache written by a newer build with an extra
        # field must not crash an older reader.
        d = SketchStats.to_dict(SketchStats(token_budget=8000))
        d["some_future_field"] = 99
        reloaded = SketchStats.from_dict(d)
        assert reloaded.token_budget == 8000

    def test_from_dict_defaults_missing_keys(self) -> None:
        # Backward-compat: a stats cache missing a field gets the default.
        reloaded = SketchStats.from_dict({"token_budget": 8000})
        assert reloaded.token_budget == 8000
        assert reloaded.total_in_degree == 0  # default


# --------------------------------------------------------------------------
# _get_or_generate_comparison_sketch — cache-read short-circuit
# --------------------------------------------------------------------------

_GEN_KWARGS = {
    "first_party_priority": True,
    "extra_excludes": [],
    "config_extraction_mode": ConfigExtractionMode.HEURISTIC,
    "verbose": False,
    "max_config_files": 15,
    "fleximax_lines": 100,
    "max_chunk_chars": 800,
    "language_proportional": False,
    "progress": False,
    "cached_results": {"profile": {}, "nodes": [], "edges": []},
}


class TestComparisonSketchCache:
    def test_cache_hit_skips_generation(self, tmp_path: Path, monkeypatch) -> None:
        budget = 32000
        fn = _generate_sketch_filename(tokens=budget, exclude_tests=False, with_source=False)
        (tmp_path / fn).write_text("cached 16x sketch text")
        cached = SketchStats(token_budget=budget, total_in_degree=100,
                             key_symbols_in_degree=42)
        (tmp_path / Path(fn).with_suffix(".stats.json")).write_text(
            json.dumps(SketchStats.to_dict(cached))
        )

        def boom(*a, **k):  # pragma: no cover - must not be called
            raise AssertionError("generate_sketch must not run on a cache hit")

        monkeypatch.setattr(cli, "generate_sketch", boom)
        stats = _get_or_generate_comparison_sketch(
            tmp_path, tmp_path, budget,
            exclude_tests=False, with_source=False, gen_kwargs=_GEN_KWARGS,
        )
        assert stats == cached

    def test_cache_miss_generates_and_caches_both(self, tmp_path: Path, monkeypatch) -> None:
        budget = 32000
        calls: list[int] = []

        def fake_gen(repo_root, **k):
            calls.append(k["max_tokens"])
            k["stats_out"].token_budget = k["max_tokens"]
            k["stats_out"].total_in_degree = 7
            return "freshly generated 16x text"

        monkeypatch.setattr(cli, "generate_sketch", fake_gen)
        stats = _get_or_generate_comparison_sketch(
            tmp_path, tmp_path, budget,
            exclude_tests=False, with_source=False, gen_kwargs=_GEN_KWARGS,
        )
        assert calls == [budget]
        assert stats.total_in_degree == 7
        # Both artifacts cached for next time.
        fn = _generate_sketch_filename(tokens=budget, exclude_tests=False, with_source=False)
        assert (tmp_path / fn).read_text() == "freshly generated 16x text"
        reloaded = SketchStats.from_dict(
            json.loads((tmp_path / Path(fn).with_suffix(".stats.json")).read_text())
        )
        assert reloaded.total_in_degree == 7

    def test_partial_cache_text_only_regenerates(self, tmp_path: Path, monkeypatch) -> None:
        # An old cache (sketch text but no stats sidecar, pre-WI-ribag) must
        # regenerate once rather than read a non-existent stats file.
        budget = 32000
        fn = _generate_sketch_filename(tokens=budget, exclude_tests=False, with_source=False)
        (tmp_path / fn).write_text("stale text without stats sidecar")
        calls: list[int] = []

        def fake_gen(repo_root, **k):
            calls.append(k["max_tokens"])
            k["stats_out"].token_budget = k["max_tokens"]
            return "regenerated"

        monkeypatch.setattr(cli, "generate_sketch", fake_gen)
        _get_or_generate_comparison_sketch(
            tmp_path, tmp_path, budget,
            exclude_tests=False, with_source=False, gen_kwargs=_GEN_KWARGS,
        )
        assert calls == [budget]
        assert (tmp_path / Path(fn).with_suffix(".stats.json")).exists()

    def test_no_cache_dir_generates_without_writing(self, tmp_path: Path, monkeypatch) -> None:
        calls: list[int] = []

        def fake_gen(repo_root, **k):
            calls.append(k["max_tokens"])
            k["stats_out"].token_budget = k["max_tokens"]
            return "generated"

        monkeypatch.setattr(cli, "generate_sketch", fake_gen)
        stats = _get_or_generate_comparison_sketch(
            tmp_path, None, 16000,
            exclude_tests=False, with_source=False, gen_kwargs=_GEN_KWARGS,
        )
        assert calls == [16000]
        assert stats.token_budget == 16000
