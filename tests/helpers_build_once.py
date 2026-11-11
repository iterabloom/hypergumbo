# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build an expensive test input once per pytest session, across xdist workers.

WHY. pytest-xdist runs here in its default ``load`` mode: tests are handed out
one at a time, so the tests of one module land on several workers, and a
module- or session-scoped fixture is built again in each worker that needs it.
Measured (WI-nupit): ``test_stable_id_floor_gate.py``'s ``runs`` fixture does
three full pipeline runs, about 150 s, and was built in four of six workers.

HOW. The first caller takes an exclusive ``flock`` on ``<name>.lock`` in a
directory every worker shares, builds, and writes ``<name>.json`` by rename.
Every later caller, in any process, finds the file and loads it. A caller that
arrives mid-build waits on the lock; only the one test that asked is held, and
the worker's other tests are not.

THE RESULT IS JSON, FOR THE BUILDER TOO. The builder returns what it reads
back, not what it built, so a test cannot pass in the worker that built (a
tuple, a ``defaultdict``, an int key) and fail in one that loaded.

A failed build writes nothing, so the next caller builds again and the failure
is reported by each test that needed the result.

The package conftest wraps this as the ``build_once`` fixture, which supplies
the shared directory. ``fcntl`` keeps this POSIX-only, like the tracker's own
store lock.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Callable


def shared_session_dir(basetemp: Path) -> Path:
    """The directory all of this session's workers can see.

    Under xdist each worker's base temp is ``<session>/popen-gwN``; without it
    the base temp is the session directory itself.
    """
    return basetemp.parent if os.environ.get("PYTEST_XDIST_WORKER") else basetemp


def build_once_in(shared: Path, name: str, build: Callable[[], Any]) -> Any:
    """``build()``'s result as JSON, computed by one process per ``shared``."""
    shared.mkdir(parents=True, exist_ok=True)
    result = shared / f"{name}.json"
    with open(shared / f"{name}.lock", "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not result.exists():
            partial = shared / f"{name}.json.{os.getpid()}.partial"
            partial.write_text(json.dumps(build()), encoding="utf-8")
            os.replace(partial, result)
        return json.loads(result.read_text(encoding="utf-8"))
