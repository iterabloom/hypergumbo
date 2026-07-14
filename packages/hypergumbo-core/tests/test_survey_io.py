# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for survey_io.load_behavior_map (WI-mokim; renamed from behavior_map_io per ADR-0042).

Covers the symmetric consumer-side helper for the producer-side --gzip
output introduced by WI-kojob. The helper must round-trip both plain
.json and .gz inputs to the same dict, preferring suffix-based
detection over magic-byte sniffing (keeps semantics aligned with the
producer's path-suffix routing in cli.cmd_run).
"""

import gzip
import importlib
import json
from pathlib import Path

import pytest

from hypergumbo_core.survey_io import (
    CANONICAL_SURVEY_FILENAME,
    LEGACY_SURVEY_FILENAMES,
    SURVEY_FILENAMES,
    SubstrateError,
    find_behavior_map,
    find_survey_in_dir,
    load_behavior_map,
    load_substrate,
)
from hypergumbo_core.schema import SCHEMA_VERSION


SAMPLE_MAP = {
    "nodes": [{"id": "sym1", "kind": "function"}],
    "edges": [{"src": "sym1", "dst": "sym2", "type": "calls"}],
    "metrics": {"total_files": 3},
}

WELL_FORMED = {
    "schema_version": SCHEMA_VERSION,
    "view": "behavior_map",
    "nodes": [{"id": "sym1", "kind": "function"}],
    "edges": [],
}


def _write(path, obj):
    path.write_text(json.dumps(obj))
    return path


def test_load_plain_json(tmp_path):
    path = tmp_path / "survey.json"
    path.write_text(json.dumps(SAMPLE_MAP))
    assert load_behavior_map(path) == SAMPLE_MAP


def test_load_gzipped_json(tmp_path):
    path = tmp_path / "survey.json.gz"
    with gzip.open(path, "wt") as f:
        json.dump(SAMPLE_MAP, f)
    assert load_behavior_map(path) == SAMPLE_MAP


def test_plain_and_gzip_round_trip_equivalent(tmp_path):
    plain = tmp_path / "survey.json"
    gz = tmp_path / "survey.json.gz"
    plain.write_text(json.dumps(SAMPLE_MAP))
    with gzip.open(gz, "wt") as f:
        json.dump(SAMPLE_MAP, f)
    assert load_behavior_map(plain) == load_behavior_map(gz)


def test_missing_file_raises_filenotfound(tmp_path):
    path = tmp_path / "nope.json"
    with pytest.raises(FileNotFoundError):
        load_behavior_map(path)


def test_accepts_string_path(tmp_path):
    path = tmp_path / "survey.json"
    path.write_text(json.dumps(SAMPLE_MAP))
    assert load_behavior_map(str(path)) == SAMPLE_MAP


def test_gzip_suffix_match_is_case_sensitive_lowercase(tmp_path):
    """Producer always writes lowercase `.gz`; consumer matches that
    literal suffix. A `.GZ` upper-case path is treated as plain text
    (mirrors the producer side which never emits upper-case suffixes)."""
    path = tmp_path / "survey.json.GZ"
    path.write_text(json.dumps(SAMPLE_MAP))
    assert load_behavior_map(path) == SAMPLE_MAP


def test_find_behavior_map_prefers_plain_over_gz(tmp_path):
    plain = tmp_path / "hg.json"
    gz = tmp_path / "hg.json.gz"
    plain.write_text("{}")
    gz.write_bytes(b"")
    assert find_behavior_map(tmp_path) == plain


def test_find_behavior_map_falls_back_to_gz(tmp_path):
    gz = tmp_path / "hg.json.gz"
    gz.write_bytes(b"")
    assert find_behavior_map(tmp_path) == gz


def test_find_behavior_map_returns_none_when_neither_exists(tmp_path):
    assert find_behavior_map(tmp_path) is None


def test_find_behavior_map_custom_basename(tmp_path):
    custom = tmp_path / "results.json.gz"
    custom.write_bytes(b"")
    assert find_behavior_map(tmp_path, basename="results.json") == custom


# --- ADR-0042 survey-rename S2/S3 foundation: constant + merged resolver -----

def test_survey_filename_constants():
    """ADR-0042: `survey.json` is the one canonical artifact name; the four
    historical names are accepted aliases; the search tuple is canonical-first."""
    assert CANONICAL_SURVEY_FILENAME == "survey.json"
    assert LEGACY_SURVEY_FILENAMES == (
        "hypergumbo.results.json", "hg.json", "bm.json", "behavior_map.json",
    )
    assert SURVEY_FILENAMES == (CANONICAL_SURVEY_FILENAME, *LEGACY_SURVEY_FILENAMES)


def test_find_survey_in_dir_finds_canonical(tmp_path):
    survey = tmp_path / "survey.json"
    survey.write_text("{}")
    assert find_survey_in_dir(tmp_path) == survey


def test_find_survey_in_dir_finds_each_legacy_alias(tmp_path):
    for name in LEGACY_SURVEY_FILENAMES:
        d = tmp_path / name.replace(".", "_")
        d.mkdir()
        legacy = d / name
        legacy.write_text("{}")
        assert find_survey_in_dir(d) == legacy


def test_find_survey_in_dir_prefers_canonical_over_legacy(tmp_path):
    (tmp_path / "hg.json").write_text("{}")
    survey = tmp_path / "survey.json"
    survey.write_text("{}")
    assert find_survey_in_dir(tmp_path) == survey


def test_find_survey_in_dir_prefers_plain_over_gz(tmp_path):
    plain = tmp_path / "survey.json"
    plain.write_text("{}")
    (tmp_path / "survey.json.gz").write_bytes(b"")
    assert find_survey_in_dir(tmp_path) == plain


def test_find_survey_in_dir_falls_back_to_gz(tmp_path):
    gz = tmp_path / "survey.json.gz"
    gz.write_bytes(b"")
    assert find_survey_in_dir(tmp_path) == gz


def test_find_survey_in_dir_canonical_gz_beats_legacy_plain(tmp_path):
    """Name-major precedence: the canonical name wins entirely, even compressed,
    over a legacy plain file — the canonical name signals current intent."""
    (tmp_path / "hg.json").write_text("{}")  # legacy plain
    canon_gz = tmp_path / "survey.json.gz"
    canon_gz.write_bytes(b"")                 # canonical gz
    assert find_survey_in_dir(tmp_path) == canon_gz


def test_find_survey_in_dir_returns_none_when_empty(tmp_path):
    assert find_survey_in_dir(tmp_path) is None


# ---------------------------------------------------------------------------
# load_substrate — the strict consumer-side loader (cli-input substrate-loader
# keystone: INV-sozop parse-guard, WI-jukah shape-guard, INV-gapib view
# discriminator, WI-marul schema_version warn-first gate).
# ---------------------------------------------------------------------------


def test_load_substrate_valid_returns_dict(tmp_path):
    p = _write(tmp_path / "survey.json", WELL_FORMED)
    assert load_substrate(p) == WELL_FORMED


def test_load_substrate_gzip_round_trips(tmp_path):
    p = tmp_path / "survey.json.gz"
    with gzip.open(p, "wt") as f:
        json.dump(WELL_FORMED, f)
    assert load_substrate(p) == WELL_FORMED


def test_load_substrate_malformed_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not valid json ")
    with pytest.raises(SubstrateError):
        load_substrate(p)


def test_load_substrate_empty_file_raises(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("")
    with pytest.raises(SubstrateError):
        load_substrate(p)


def test_load_substrate_non_dict_root_raises(tmp_path):
    p = _write(tmp_path / "arr.json", [1, 2, 3])
    with pytest.raises(SubstrateError):
        load_substrate(p)


def test_load_substrate_missing_nodes_raises(tmp_path):
    """WI-jukah: a dict lacking the structural ``nodes`` key is not a
    behavior map — reject it rather than silently emitting empty output."""
    p = _write(tmp_path / "noshape.json", {"schema_version": SCHEMA_VERSION})
    with pytest.raises(SubstrateError):
        load_substrate(p)


def test_load_substrate_wrong_view_raises(tmp_path):
    """INV-gapib: a document whose ``view`` differs from the expected view is
    rejected. Includes ``nodes`` so the *view* guard fires (not the shape
    guard) — a nodes-bearing doc with the wrong view is the case the view
    discriminator uniquely catches."""
    p = _write(
        tmp_path / "wrongview.json",
        {"schema_version": SCHEMA_VERSION, "view": "tiered", "nodes": []},
    )
    with pytest.raises(SubstrateError, match="wrong view"):
        load_substrate(p)


def test_load_substrate_absent_view_accepted(tmp_path):
    """A minimal/legacy map with no ``view`` field (but the right shape) is
    accepted — INV-gapib rejects a *wrong* view, not a *missing* one."""
    p = _write(
        tmp_path / "legacy.json",
        {"schema_version": SCHEMA_VERSION, "nodes": [], "edges": []},
    )
    assert load_substrate(p) == {
        "schema_version": SCHEMA_VERSION, "nodes": [], "edges": [],
    }


def test_load_substrate_expected_view_param(tmp_path):
    p = _write(
        tmp_path / "compact.json",
        {"schema_version": SCHEMA_VERSION, "view": "compact", "nodes": []},
    )
    assert load_substrate(p, expected_view="compact")["view"] == "compact"


def test_load_substrate_schema_version_mismatch_warns(tmp_path, capsys):
    """WI-marul: a differing schema_version warns (warn-first) but still loads."""
    p = _write(
        tmp_path / "old.json",
        {"schema_version": "0.0.1-ancient", "nodes": [], "edges": []},
    )
    result = load_substrate(p)
    assert result["schema_version"] == "0.0.1-ancient"
    err = capsys.readouterr().err
    assert "0.0.1-ancient" in err
    assert SCHEMA_VERSION in err


def test_load_substrate_absent_schema_version_warns(tmp_path, capsys):
    """WI-marul: an absent schema_version warns (cannot verify) but loads."""
    p = _write(tmp_path / "nover.json", {"nodes": [], "edges": []})
    load_substrate(p)
    assert "schema_version" in capsys.readouterr().err


def test_load_substrate_matching_version_is_silent(tmp_path, capsys):
    # ADR-0042: the canonical name + a matching schema_version is truly silent
    # (a legacy basename would now emit a deprecation warning — see below).
    _write(tmp_path / CANONICAL_SURVEY_FILENAME, WELL_FORMED)
    load_substrate(tmp_path / CANONICAL_SURVEY_FILENAME)
    assert capsys.readouterr().err == ""


# ─────────────────────────────────────────────────────────────────────────
# WI-didif (ADR-0042): legacy survey-filename acceptance + deprecation warning
# ─────────────────────────────────────────────────────────────────────────


def test_load_substrate_legacy_name_warns(tmp_path, capsys):
    """A survey substrate loaded under a legacy basename still loads, but warns
    to stderr, naming the alias found and the canonical survey.json."""
    p = _write(tmp_path / "hg.json", WELL_FORMED)
    assert load_substrate(p) == WELL_FORMED
    err = capsys.readouterr().err
    assert "deprecated" in err.lower()
    assert "hg.json" in err
    assert CANONICAL_SURVEY_FILENAME in err


def test_load_substrate_canonical_name_no_deprecation_warning(tmp_path, capsys):
    """The canonical survey.json loads without a deprecation warning."""
    p = _write(tmp_path / CANONICAL_SURVEY_FILENAME, WELL_FORMED)
    load_substrate(p)
    assert "deprecated" not in capsys.readouterr().err.lower()


@pytest.mark.parametrize("name", list(LEGACY_SURVEY_FILENAMES))
def test_load_substrate_each_legacy_name_warns(tmp_path, capsys, name):
    """Every declared legacy alias triggers the deprecation warning."""
    p = _write(tmp_path / name, WELL_FORMED)
    load_substrate(p)
    err = capsys.readouterr().err
    assert "deprecated" in err.lower()
    assert name in err


def test_load_substrate_legacy_gzip_name_warns(tmp_path, capsys):
    """A gzipped legacy alias (hg.json.gz) warns — the .gz suffix is stripped
    before matching against the alias list."""
    p = tmp_path / "hg.json.gz"
    with gzip.open(p, "wt") as f:
        json.dump(WELL_FORMED, f)
    load_substrate(p)
    err = capsys.readouterr().err
    assert "deprecated" in err.lower()
    assert "hg.json" in err


def test_cli_has_no_raw_behavior_map_reads():
    """Regression guard: every CLI consumer must route through
    ``load_behavior_map``. Catches future hand-rolled
    ``json.loads(path.read_text())`` reintroductions in ``cli.py`` that
    would silently drop ``.gz`` support."""
    cli_path = (
        Path(__file__).parent.parent
        / "src"
        / "hypergumbo_core"
        / "cli.py"
    )
    source = cli_path.read_text()
    assert "json.loads(input_path.read_text())" not in source
    assert "json.loads(input_file.read_text())" not in source


# ─────────────────────────────────────────────────────────────────────────
# ADR-0042 (WI-kisoj): behavior_map_io is a deprecation shim for survey_io.
# Lives here (not a separate module) so it stays inside the change-detection
# manifest CI runs — the shim is the ONLY importer of behavior_map_io, so a
# test in an un-selected file would leave the shim at 0% coverage in CI.
# ─────────────────────────────────────────────────────────────────────────


def test_behavior_map_io_shim_reexports_survey_io_and_warns() -> None:
    import hypergumbo_core.behavior_map_io as shim
    from hypergumbo_core import survey_io

    # A fresh (re)import fires the module-level DeprecationWarning, regardless
    # of whether an earlier test already imported (and cached) the shim.
    with pytest.warns(DeprecationWarning, match="survey_io"):
        importlib.reload(shim)

    # Every re-exported name is the exact object from the new home.
    assert shim.CANONICAL_SURVEY_FILENAME == survey_io.CANONICAL_SURVEY_FILENAME
    assert shim.LEGACY_SURVEY_FILENAMES == survey_io.LEGACY_SURVEY_FILENAMES
    assert shim.SURVEY_FILENAMES == survey_io.SURVEY_FILENAMES
    assert shim.SubstrateError is survey_io.SubstrateError
    assert shim.load_substrate is survey_io.load_substrate
    assert shim.load_behavior_map is survey_io.load_behavior_map
    assert shim.find_behavior_map is survey_io.find_behavior_map
    assert shim.find_survey_in_dir is survey_io.find_survey_in_dir
    assert set(shim.__all__) == {
        "CANONICAL_SURVEY_FILENAME",
        "LEGACY_SURVEY_FILENAMES",
        "SURVEY_FILENAMES",
        "SubstrateError",
        "find_behavior_map",
        "find_survey_in_dir",
        "load_behavior_map",
        "load_substrate",
    }

