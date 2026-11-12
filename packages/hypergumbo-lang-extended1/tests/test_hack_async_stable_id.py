# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Hack ``async function`` gets a different stable_id from the same
function without ``async`` (ADR-0035 §1); the non-async id is pinned to the
previous scheme's value."""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_extended1.hack import analyze_hack

SYNC = "<?hh\nfunction handle(int $req): Awaitable<int> { return $req; }\n"
ASYNC = "<?hh\nasync function handle(int $req): Awaitable<int> { return $req; }\n"


def _stable_id(tmp: Path, src: str) -> str:
    tmp.mkdir()
    (tmp / "m.hack").write_text(src)
    ids = [s.stable_id for s in analyze_hack(tmp).symbols
           if s.name == "handle" and s.kind == "function"]
    assert len(ids) == 1 and ids[0] is not None
    return ids[0]


def test_async_changes_the_stable_id(tmp_path: Path) -> None:
    sync = _stable_id(tmp_path / "sync", SYNC)
    assert sync == "sha256:8c0b3a905068840a"
    assert _stable_id(tmp_path / "async", ASYNC) != sync
