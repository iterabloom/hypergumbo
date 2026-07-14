# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-vatuf (ADR-0042, S2-CLI): ``survey`` verb + ``survey.json`` default name.

``hypergumbo survey`` is the primary analysis verb producing ``survey.json``;
``hypergumbo run`` remains a deprecated one-minor-version alias that warns on
stderr (fully functional until window-close). The default cache-write basename
and every internal cache-reader move to the canonical ``survey.json``; legacy
basenames (``hypergumbo.results.json`` / ``hg.json`` / ``bm.json`` /
``behavior_map.json``) still resolve on read via ``find_survey_in_dir`` so
pre-rename caches keep working. See ADR-0042 §3/§4.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from hypergumbo_core.behavior_map_io import CANONICAL_SURVEY_FILENAME
from hypergumbo_core.cli import build_parser, cmd_run, main
from hypergumbo_core.sketch import _peek_cached_results
from hypergumbo_core.sketch_embeddings import _get_results_cache_dir
from hypergumbo_core.test_masking import find_latest_behavior_map


def _has_hypergumbo_meta() -> bool:
    try:
        import hypergumbo

        del hypergumbo
        return True
    except ImportError:  # pragma: no cover - meta pkg present in CI/editable env
        return False


# --------------------------------------------------------------------------
# Verb registration: survey primary, run a hidden deprecated alias
# --------------------------------------------------------------------------


def test_build_parser_registers_survey_as_primary_verb() -> None:
    ns = build_parser().parse_args(["survey", "."])
    assert ns.command == "survey"
    assert ns.func is cmd_run


def test_build_parser_run_remains_a_working_alias() -> None:
    # ``run`` still parses and dispatches to cmd_run; argparse records the
    # actually-typed verb in ``command`` (that is how the warning is gated).
    ns = build_parser().parse_args(["run", "."])
    assert ns.command == "run"
    assert ns.func is cmd_run


# --------------------------------------------------------------------------
# Deprecation warning: fires for ``run``, silent for ``survey``
# --------------------------------------------------------------------------


def _min_args(command: str | None, path: Path) -> argparse.Namespace:
    ns = argparse.Namespace(path=str(path))
    if command is not None:
        ns.command = command
    return ns


def test_run_verb_emits_deprecation_warning(tmp_path: Path, capsys) -> None:
    # A nonexistent path makes cmd_run return 1 early; the verb-level
    # deprecation warning fires first, before any path validity check.
    rc = cmd_run(_min_args("run", tmp_path / "nope"))
    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "deprecated" in err
    assert "survey" in err


def test_survey_verb_does_not_warn(tmp_path: Path, capsys) -> None:
    rc = cmd_run(_min_args("survey", tmp_path / "nope"))
    assert rc == 1
    assert "deprecated" not in capsys.readouterr().err.lower()


def test_cmd_run_without_command_attr_does_not_warn(tmp_path: Path, capsys) -> None:
    # Direct callers may build a Namespace lacking ``command``; the getattr
    # guard must neither raise nor warn.
    rc = cmd_run(_min_args(None, tmp_path / "nope"))
    assert rc == 1
    assert "deprecated" not in capsys.readouterr().err.lower()


# --------------------------------------------------------------------------
# Default write basename: survey.json (not hypergumbo.results.json)
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    not _has_hypergumbo_meta(), reason="requires hypergumbo meta-package"
)
def test_survey_default_write_basename_is_survey_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def f():\n    return 1\n")

    rc = main(["survey", str(repo)])
    assert rc == 0

    xdg = tmp_path / "xdg"
    assert list(xdg.rglob(CANONICAL_SURVEY_FILENAME)), (
        "default write did not produce survey.json in the cache"
    )
    assert not list(xdg.rglob("hypergumbo.results.json")), (
        "legacy basename must no longer be the default write name"
    )


# --------------------------------------------------------------------------
# Internal cache-readers resolve the new name AND legacy names
# --------------------------------------------------------------------------


def test_peek_cached_results_finds_survey_json(tmp_path: Path) -> None:
    cache_dir = _get_results_cache_dir(tmp_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / CANONICAL_SURVEY_FILENAME).write_text(
        json.dumps({"nodes": [{"path": "a.py"}]})
    )
    got = _peek_cached_results(tmp_path)
    assert got is not None
    assert got["nodes"][0]["path"] == "a.py"


def test_peek_cached_results_still_finds_legacy_results_json(tmp_path: Path) -> None:
    cache_dir = _get_results_cache_dir(tmp_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "hypergumbo.results.json").write_text(
        json.dumps({"nodes": [{"path": "b.py"}]})
    )
    got = _peek_cached_results(tmp_path)
    assert got is not None
    assert got["nodes"][0]["path"] == "b.py"


def test_find_latest_behavior_map_finds_survey_json(tmp_path: Path) -> None:
    with patch(
        "hypergumbo_core.sketch_embeddings._get_repo_fingerprint",
        return_value="fp",
    ), patch(
        "hypergumbo_core.sketch_embeddings._get_xdg_cache_base",
        return_value=tmp_path,
    ):
        analyzer_dir = tmp_path / "fp" / "results" / "state" / "analyzer1"
        analyzer_dir.mkdir(parents=True)
        survey = analyzer_dir / CANONICAL_SURVEY_FILENAME
        survey.write_text("{}")
        assert find_latest_behavior_map(tmp_path / "repo") == survey
