# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for behavior_map_io.load_behavior_map (WI-mokim).

Covers the symmetric consumer-side helper for the producer-side --gzip
output introduced by WI-kojob. The helper must round-trip both plain
.json and .gz inputs to the same dict, preferring suffix-based
detection over magic-byte sniffing (keeps semantics aligned with the
producer's path-suffix routing in cli.cmd_run).
"""

import gzip
import json
from pathlib import Path

import pytest

from hypergumbo_core.behavior_map_io import (
    SubstrateError,
    find_behavior_map,
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
    path = tmp_path / "bm.json"
    path.write_text(json.dumps(SAMPLE_MAP))
    assert load_behavior_map(path) == SAMPLE_MAP


def test_load_gzipped_json(tmp_path):
    path = tmp_path / "bm.json.gz"
    with gzip.open(path, "wt") as f:
        json.dump(SAMPLE_MAP, f)
    assert load_behavior_map(path) == SAMPLE_MAP


def test_plain_and_gzip_round_trip_equivalent(tmp_path):
    plain = tmp_path / "bm.json"
    gz = tmp_path / "bm.json.gz"
    plain.write_text(json.dumps(SAMPLE_MAP))
    with gzip.open(gz, "wt") as f:
        json.dump(SAMPLE_MAP, f)
    assert load_behavior_map(plain) == load_behavior_map(gz)


def test_missing_file_raises_filenotfound(tmp_path):
    path = tmp_path / "nope.json"
    with pytest.raises(FileNotFoundError):
        load_behavior_map(path)


def test_accepts_string_path(tmp_path):
    path = tmp_path / "bm.json"
    path.write_text(json.dumps(SAMPLE_MAP))
    assert load_behavior_map(str(path)) == SAMPLE_MAP


def test_gzip_suffix_match_is_case_sensitive_lowercase(tmp_path):
    """Producer always writes lowercase `.gz`; consumer matches that
    literal suffix. A `.GZ` upper-case path is treated as plain text
    (mirrors the producer side which never emits upper-case suffixes)."""
    path = tmp_path / "bm.json.GZ"
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


# ---------------------------------------------------------------------------
# load_substrate — the strict consumer-side loader (cli-input substrate-loader
# keystone: INV-sozop parse-guard, WI-jukah shape-guard, INV-gapib view
# discriminator, WI-marul schema_version warn-first gate).
# ---------------------------------------------------------------------------


def test_load_substrate_valid_returns_dict(tmp_path):
    p = _write(tmp_path / "bm.json", WELL_FORMED)
    assert load_substrate(p) == WELL_FORMED


def test_load_substrate_gzip_round_trips(tmp_path):
    p = tmp_path / "bm.json.gz"
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
    _write(tmp_path / "bm.json", WELL_FORMED)
    load_substrate(tmp_path / "bm.json")
    assert capsys.readouterr().err == ""


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

