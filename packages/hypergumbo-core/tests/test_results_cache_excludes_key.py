# SPDX-License-Identifier: AGPL-3.0-or-later
"""``--exclude`` is part of the results-cache key (WI-tisuj).

A cached survey is reused only by runs with the same analysis configuration
(backends: WI-givib; ``--trace-deps``: WI-bapal). ``-e`` / ``--exclude`` was
not in the key, so ``hypergumbo survey repo -e lib`` REPLACED the plain
survey of ``repo`` in the one results directory, and a later
``hypergumbo search lo repo`` -- a read command the user never asked to
exclude anything -- answered "No symbols found matching lo".

The state segment now carries ``-ex-<digest>`` when excludes are given.
Read commands take no ``--exclude`` and look under the plain key, so an
excluding survey is never what they find (the same rule as a one-off
backend flag).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import _discover_input_file, run_survey
from hypergumbo_core.discovery import exclude_cache_token
from hypergumbo_core.sketch_embeddings import _get_results_cache_dir
from hypergumbo_core.survey_io import find_survey_in_dir


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    root = tmp_path / "exfx"
    (root / "lib").mkdir(parents=True)
    (root / "a.py").write_text("def hi():\n    return 1\n")
    (root / "lib" / "b.py").write_text("def lo():\n    return 2\n")
    return root


class TestExcludeCacheToken:
    def test_nothing_to_exclude_is_the_empty_token(self) -> None:
        assert exclude_cache_token(None) == ""
        assert exclude_cache_token([]) == ""
        assert exclude_cache_token(["", "   "]) == ""

    def test_order_duplicates_and_whitespace_do_not_matter(self) -> None:
        assert exclude_cache_token(["lib", "*.json"]) == exclude_cache_token(["*.json", " lib ", "lib"])

    def test_different_patterns_give_different_tokens(self) -> None:
        assert exclude_cache_token(["lib"]) != exclude_cache_token(["vendor"])

    def test_token_is_filesystem_safe_and_short(self) -> None:
        token = exclude_cache_token(["lib/**", "*.json"])
        assert token.startswith("ex-") and len(token) == 15
        assert token.replace("-", "").isalnum()


class TestExcludesKeyTheResultsCache:
    def test_the_key_moves_only_when_excludes_are_given(self, repo: Path) -> None:
        plain = _get_results_cache_dir(repo)
        assert _get_results_cache_dir(repo, extra_excludes=[]) == plain
        with_lib = _get_results_cache_dir(repo, extra_excludes=["lib"])
        assert with_lib != plain
        assert with_lib.parent.name == f"{plain.parent.name}-{exclude_cache_token(['lib'])}"
        assert _get_results_cache_dir(repo, extra_excludes=["vendor"]) != with_lib

    def test_an_excluding_survey_does_not_replace_the_plain_one(self, repo: Path) -> None:
        run_survey(repo, progress=False, include_sketch_precomputed=False, enable_handler_slices=False)
        run_survey(repo, extra_excludes=["lib"], progress=False, include_sketch_precomputed=False, enable_handler_slices=False)

        # The read path (search, explain, ...) finds the plain survey, lo included.
        found = _discover_input_file(repo)
        assert found is not None
        assert found.parent == _get_results_cache_dir(repo)
        names = {n["name"] for n in json.loads(found.read_text())["nodes"]}
        assert "lo" in names and "hi" in names

        # The excluding survey lives under its own key, without lib/.
        excluded = find_survey_in_dir(_get_results_cache_dir(repo, extra_excludes=["lib"]))
        assert excluded is not None
        ex_names = {n["name"] for n in json.loads(excluded.read_text())["nodes"]}
        assert "hi" in ex_names and "lo" not in ex_names
