# SPDX-License-Identifier: AGPL-3.0-or-later
"""The ``build_once`` fixture: one expensive result per session, not per worker.

pytest-xdist runs here in its default ``load`` mode, which hands tests out one
at a time. A module-scoped fixture is therefore built again in EVERY worker
that receives one of its tests. Measured on ``test_stable_id_floor_gate.py``:
its ``runs`` fixture (three full pipeline runs, about 150 s) was built in four
of six workers, 621 serial seconds for a file whose tests need it once
(WI-nupit). ``build_once`` keeps the result in a file every worker of the
session can read, behind a lock, so the first worker builds and the rest load.
"""
from __future__ import annotations

import json
import multiprocessing
from pathlib import Path


def test_the_second_caller_loads_what_the_first_built(build_once) -> None:
    calls = []

    def build() -> dict:
        calls.append(1)
        return {"n": len(calls), "items": [1, 2]}

    first = build_once("test-build-once-a", build)
    second = build_once("test-build-once-a", build)
    assert first == second == {"n": 1, "items": [1, 2]}
    assert len(calls) == 1


def test_names_do_not_share_a_result(build_once) -> None:
    assert build_once("test-build-once-b", lambda: "b") == "b"
    assert build_once("test-build-once-c", lambda: "c") == "c"


def test_the_builder_gets_what_every_other_caller_gets(build_once) -> None:
    """The first caller sees the JSON round trip too, so no test can pass in
    the worker that built and fail in one that loaded (a tuple is a list)."""
    assert build_once("test-build-once-d", lambda: {"pair": (1, 2)}) == {"pair": [1, 2]}


def test_a_failed_build_is_not_cached(build_once) -> None:
    def explode() -> None:
        raise RuntimeError("no")

    try:
        build_once("test-build-once-e", explode)
    except RuntimeError:
        pass
    assert build_once("test-build-once-e", lambda: "second try") == "second try"


_HELPER = Path(__file__).resolve().parents[3] / "tests" / "helpers_build_once.py"


def _worker(shared: str, name: str, log: str) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("helpers_build_once", _HELPER)
    assert spec and spec.loader
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    build_once_in = helper.build_once_in

    def build() -> str:
        with open(log, "a") as fh:
            fh.write("built\n")
        import time
        time.sleep(0.3)
        return "value"

    assert build_once_in(Path(shared), name, build) == "value"


def test_two_processes_build_once_between_them(tmp_path: Path) -> None:
    """The case the fixture exists for: separate processes, one shared dir."""
    log = tmp_path / "builds.log"
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_worker, args=(str(tmp_path), "shared", str(log)))
             for _ in range(3)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(30)
    assert [proc.exitcode for proc in procs] == [0, 0, 0]
    assert log.read_text().splitlines() == ["built"]
    assert json.loads((tmp_path / "shared.json").read_text()) == "value"
