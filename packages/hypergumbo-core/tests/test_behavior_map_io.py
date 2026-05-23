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

from hypergumbo_core.behavior_map_io import find_behavior_map, load_behavior_map


SAMPLE_MAP = {
    "nodes": [{"id": "sym1", "kind": "function"}],
    "edges": [{"src": "sym1", "dst": "sym2", "type": "calls"}],
    "metrics": {"total_files": 3},
}


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

