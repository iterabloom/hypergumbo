# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Hack ``async function`` gets a different stable_id from the same
function without ``async`` (ADR-0035 §1); the non-async id is pinned. Its
value moved once, with stable-id scheme v19, when a function's hashed kind
became ``function`` instead of the role word ``fn`` (WI-lilab)."""

from __future__ import annotations

from pathlib import Path

from hypergumbo_lang_extended1.hack import analyze_hack
from hypergumbo_core.identity_schemes import STABLE_ID_PREFIX

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
    assert sync == f"{STABLE_ID_PREFIX}f830b09c22ffb63d"
    assert _stable_id(tmp_path / "async", ASYNC) != sync
