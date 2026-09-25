# SPDX-License-Identifier: AGPL-3.0-or-later
"""INV-hugit: a manifest is not a dispatcher, and a demotion is not a secret.

WHAT WAS WRONG. ``dead-code-maybe``'s cross-language demoter hard-excluded any
candidate whose short name appeared as a SUBSTRING in at least
``--cross-lang-threshold`` (default 3) files of a *different* language. Its
``_EXT_TO_LANG`` mapped ``.json`` / ``.yaml`` / ``.yml`` / ``.toml`` / ``.xml``
/ ``.html`` to a pseudo-language ``"config"``, so a manifest counted as another
language. The demoter's stated rationale is cross-language DISPATCH -- an HTTP
route, an RPC method, an FFI name -- and a YAML file mentioning a Python symbol
is a manifest, not a dispatcher.

MEASURED ON THIS REPOSITORY before the change: 207 of 2,489 candidates demoted,
of which **112 (54.1%) had fewer than three hits in any real other-language
file** and were removed on manifest substrings alone. The shape of the worst
ones is the argument: ``extract`` (66 config hits, 1 code), ``matches`` (30, 1),
and ``_label`` (24, **0**) -- a leading-underscore private helper, which has no
cross-language dispatch story at all, deleted from the report by substring
collisions in YAML.

WHY IT IS THE FAIL-OPEN DIRECTION. The demoter REMOVES findings and published
only a count, so a wrongly demoted candidate was unreviewable: a reader could
not tell "reachable" from "suppressed by a string collision". INV-rolok's own
sizing claim -- *"0 of 2,152 dead candidates are ``__init__``, so Python
constructors are not currently mis-flagged"* -- was manufactured by this bug.

THE TWO HALVES, WHICH ARE ONE CHANGE. (A) Config-family files stop counting
toward the threshold, because ``config`` is not a language; this is what the
invariant's own statement compels rather than a judgement made here. (D) The
demotions that remain -- the ones resting on real other-language code -- are
DISCLOSED as a bucket instead of silently dropped, matching how
``entrypoint_only_dead`` and ``test_only_reachable_candidates`` are already
handled in the same summary. Shipping (A) alone would be a one-directional
loosening; shipping (D) alone would leave the statement violated.

NOTHING IS DISCARDED. The config hit count is still computed and is carried on
every candidate as ``config_name_hits``, so a reviewer who wants the old signal
can still read it -- it simply no longer makes the decision by itself.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from hypergumbo_core.cli import (
    _compute_cross_language_hits,
    cmd_dead_code_maybe,
)


def _candidate(name: str, *, language: str = "python") -> dict:
    return {
        "id": f"{language}:src/mod.py:1-2:{name}:function",
        "name": name,
        "kind": "function",
        "language": language,
        "path": "src/mod.py",
    }


def _repo_with(tmp_path: Path, *, config_files: int = 0, go_files: int = 0,
               needle: str = "extract") -> Path:
    """A repo whose manifests and/or Go sources mention ``needle``."""
    for i in range(config_files):
        (tmp_path / f"conf{i}.yaml").write_text(f"job: {needle}\n")
    for i in range(go_files):
        d = tmp_path / f"pkg{i}"
        d.mkdir()
        (d / "main.go").write_text(f'package pkg{i}\n// calls {needle}\n')
    return tmp_path


class TestConfigIsNotALanguage:
    """(A) A manifest substring is not evidence of cross-language dispatch."""

    def test_manifest_hits_do_not_count_as_cross_language(
        self, tmp_path: Path,
    ) -> None:
        cand = _candidate("extract")
        hits = _compute_cross_language_hits(
            [cand], _repo_with(tmp_path, config_files=5),
        )
        assert hits.code.get(cand["id"], 0) == 0

    def test_manifest_hits_are_still_counted_and_reported(
        self, tmp_path: Path,
    ) -> None:
        """Nothing is discarded -- the signal is demoted, not deleted."""
        cand = _candidate("extract")
        hits = _compute_cross_language_hits(
            [cand], _repo_with(tmp_path, config_files=5),
        )
        assert hits.config.get(cand["id"], 0) == 5

    def test_a_real_other_language_file_still_counts(
        self, tmp_path: Path,
    ) -> None:
        """The difference arm: the demoter still does its actual job."""
        cand = _candidate("extract")
        hits = _compute_cross_language_hits(
            [cand], _repo_with(tmp_path, go_files=4),
        )
        assert hits.code.get(cand["id"], 0) == 4
        assert hits.config.get(cand["id"], 0) == 0

    def test_a_private_dunder_name_is_not_dispatched_from_yaml(
        self, tmp_path: Path,
    ) -> None:
        """``_label`` and ``__init__``: the shapes that made this visible."""
        for name in ("_label", "__init__"):
            cand = _candidate(name)
            hits = _compute_cross_language_hits(
                [cand], _repo_with(tmp_path, config_files=9, needle=name),
            )
            assert hits.code.get(cand["id"], 0) == 0, name


def _run(tmp_path: Path, nodes: list[dict], **overrides) -> dict:
    """Run the command over ``tmp_path/repo``.

    The behavior map is written OUTSIDE the scanned root on purpose: ``.json``
    is one of the manifest extensions the collision scan reads, so a map left
    inside the repo contributes a config hit of its own and the counts below
    stop being exact. That is a fixture artefact, not the behaviour under
    test -- but it is the kind that makes a number look almost right.
    """
    repo = tmp_path / "repo"
    bm = tmp_path / "hg.json"
    bm.write_text(json.dumps(
        {"schema_version": "0.20.13", "nodes": nodes, "edges": []},
    ))
    args = argparse.Namespace(
        path=str(repo), input=str(bm), format="json",
        seeds="entrypoints", min_confidence=0.0, **overrides,
    )
    captured = io.StringIO()
    old = sys.stdout
    sys.stdout = captured
    try:
        cmd_dead_code_maybe(args)
    finally:
        sys.stdout = old
    return json.loads(captured.getvalue())


def _dead_fixture(tmp_path: Path, name: str, *, config_files: int,
                  go_files: int) -> list[dict]:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "mod.py").write_text(f"def {name}():\n    pass\n")
    _repo_with(repo, config_files=config_files, go_files=go_files,
               needle=name)
    return [
        {"id": f"python:src/mod.py:1-2:{name}:function", "name": name,
         "kind": "function", "language": "python", "path": "src/mod.py",
         "span": {"start_line": 1, "end_line": 2}},
        {"id": "python:src/main.py:1-1:main:function", "name": "main",
         "kind": "function", "language": "python", "path": "src/main.py",
         "span": {"start_line": 1, "end_line": 1},
         "meta": {"is_main": True}},
    ]


class TestTheCandidateSurvivesAManifestCollision:
    """End to end: the 112-candidate cohort comes back."""

    def test_a_config_only_collision_no_longer_demotes(
        self, tmp_path: Path,
    ) -> None:
        nodes = _dead_fixture(tmp_path, "extract", config_files=6, go_files=0)
        out = _run(tmp_path, nodes)
        assert [d["name"] for d in out["dead_candidates"]
                if d["name"] == "extract"] == ["extract"]
        assert out["summary"]["demoted_cross_language"] == 0

    def test_the_surviving_candidate_carries_the_manifest_count(
        self, tmp_path: Path,
    ) -> None:
        nodes = _dead_fixture(tmp_path, "extract", config_files=6, go_files=0)
        out = _run(tmp_path, nodes)
        row = next(d for d in out["dead_candidates"] if d["name"] == "extract")
        assert (row["cross_language_hits"], row["config_name_hits"]) == (0, 6)

    def test_a_real_cross_language_collision_still_demotes(
        self, tmp_path: Path,
    ) -> None:
        """The control. Without this the change is an unconditional loosening
        and every assertion above would pass on a demoter that does nothing."""
        nodes = _dead_fixture(tmp_path, "extract", config_files=0, go_files=4)
        out = _run(tmp_path, nodes)
        assert [d["name"] for d in out["dead_candidates"]
                if d["name"] == "extract"] == []
        assert out["summary"]["demoted_cross_language"] == 1


class TestTheDemotionIsDisclosed:
    """(D) 'must at minimum be disclosed rather than silently dropped'."""

    def test_a_demoted_candidate_is_listed_not_just_counted(
        self, tmp_path: Path,
    ) -> None:
        nodes = _dead_fixture(tmp_path, "extract", config_files=0, go_files=4)
        out = _run(tmp_path, nodes)
        listed = out["cross_language_demoted"]
        assert [r["name"] for r in listed] == ["extract"]
        assert listed[0]["cross_language_hits"] == 4

    def test_the_bucket_is_empty_not_absent_when_nothing_is_demoted(
        self, tmp_path: Path,
    ) -> None:
        """ABSENT != EMPTY. A reader must be able to tell 'the demoter ran and
        took nothing' from 'this build has no such bucket'."""
        nodes = _dead_fixture(tmp_path, "extract", config_files=6, go_files=0)
        out = _run(tmp_path, nodes)
        assert out["cross_language_demoted"] == []

    def test_the_listing_and_the_count_agree(self, tmp_path: Path) -> None:
        nodes = _dead_fixture(tmp_path, "extract", config_files=0, go_files=4)
        out = _run(tmp_path, nodes)
        assert (len(out["cross_language_demoted"])
                == out["summary"]["demoted_cross_language"])

    def test_disabling_the_demoter_empties_the_bucket(
        self, tmp_path: Path,
    ) -> None:
        nodes = _dead_fixture(tmp_path, "extract", config_files=0, go_files=4)
        out = _run(tmp_path, nodes, cross_lang_threshold=0)
        assert out["cross_language_demoted"] == []
        assert [d["name"] for d in out["dead_candidates"]
                if d["name"] == "extract"] == ["extract"]
