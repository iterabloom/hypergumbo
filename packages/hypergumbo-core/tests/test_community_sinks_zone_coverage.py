# SPDX-License-Identifier: AGPL-3.0-or-later
"""Community sinks add findings but are not a zone's coverage (WI-mizog, ADR-0061 ruling 2).

verify-claims withholds a clean taint verdict when a present language has no
sink in the claim's zone (INV-pivam, WI-sojok's ``_sink_zone_gaps``). It counted
ANY sink, community rows included. Haskell's ``network`` zone is covered only by
community rows (``Network.HTTP.Client``), so "untrusted input never reaches the
network" on a Haskell repo read ``confirmed`` by default and ``inconclusive``
under ``--no-default-overlays``: the clean verdict rested on unvouched rows
alone. ADR-0061 ruling 2: community rows "may add findings, but never make a
verdict cleaner", and a verdict is cleaner whenever it moves toward
``confirmed``. So the gate counts vouched sinks, and a flow a community sink
FINDS still reads ``violated``.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from hypergumbo_core.cli import main

_FETCH = '''module Main where

import Network.HTTP.Client (newManager, httpLbs, parseRequest)
import Network.HTTP.Client.TLS (tlsManagerSettings)

fetch :: String -> IO ()
fetch url = do
  manager <- newManager tlsManagerSettings
  request <- parseRequest url
  _ <- httpLbs request manager
  return ()

'''
_NO_INPUT = _FETCH + 'main :: IO ()\nmain = fetch "https://example.com"\n'
_INPUT = _FETCH + "main :: IO ()\nmain = do\n  line <- getLine\n  fetch line\n"


def _verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, *flags: str) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Main.hs").write_text(source)
    claims = tmp_path / "claims.yaml"
    claims.write_text(
        "claims:\n  - id: c\n    text: t\n    constraint:\n      taint_flow:\n"
        "        source_taint: untrusted_input\n        prohibited_sink_zone: network\n"
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims), "--format", "json", *flags])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def test_a_zone_only_community_rows_cover_is_not_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, _NO_INPUT)
    assert verdict["verdict"] == "inconclusive", verdict
    assert "only community rows in the 'network' zone" in verdict["details"]
    assert "haskell" in verdict["details"]


def test_without_community_rows_the_reason_is_that_there_is_no_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    verdict = _verdict(tmp_path, monkeypatch, _NO_INPUT, "--no-default-overlays")
    assert verdict["verdict"] == "inconclusive", verdict
    assert "no sink in the 'network' zone" in verdict["details"]


def test_a_flow_a_community_sink_finds_still_violates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adding half of ruling 2: the community row's finding stands."""
    verdict = _verdict(tmp_path, monkeypatch, _INPUT)
    assert verdict["verdict"] == "violated", verdict
    assert "httpLbs" in verdict["details"]
