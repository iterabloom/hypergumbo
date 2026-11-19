# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Dart ``async`` / ``async*`` body marker changes the function's stable_id.

Dart writes the marker on the function BODY, not the signature. A function
that returns a ``Future`` without ``async`` keeps the unmarked id (pinned):
the keyword is the signal, not the return type (ADR-0035 §1). The pinned
value hashes the whole return type ``Future<int>``; it was
``hgstableid15:ee2ecac25472836f`` (a stableid-v15 id) while the return type
was hashed as ``Future``.
"""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_common.dart import analyze_dart
from hypergumbo_core.identity_schemes import STABLE_ID_PREFIX

SYNC = "Future<int> handle(int req) { return Future.value(req); }\n"
ASYNC = "Future<int> handle(int req) async { return req; }\n"
STREAM = "Stream<int> handle(int req) async* { yield req; }\n"
STREAM_SYNC = "Stream<int> handle(int req) { return Stream.value(req); }\n"


def _stable_id(tmp: Path, src: str) -> str:
    tmp.mkdir()
    (tmp / "m.dart").write_text(src)
    ids = [s.stable_id for s in analyze_dart(tmp).symbols
           if s.name == "handle" and s.kind == "function"]
    assert len(ids) == 1 and ids[0] is not None
    return ids[0]


def test_async_body_changes_the_stable_id(tmp_path: Path) -> None:
    sync = _stable_id(tmp_path / "sync", SYNC)
    assert sync == f"{STABLE_ID_PREFIX}17d9b2121f747146"
    assert _stable_id(tmp_path / "async", ASYNC) != sync


def test_async_star_body_changes_the_stable_id(tmp_path: Path) -> None:
    assert (_stable_id(tmp_path / "stream", STREAM)
            != _stable_id(tmp_path / "plain", STREAM_SYNC))
