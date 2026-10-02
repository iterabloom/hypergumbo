# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-pass cost clock: a pass's wall time AND its own-thread CPU time.

What it is for (WI-nuvam)
-------------------------
``AnalysisRun.duration_ms`` is wall-clock time, and passes do not run alone:
``run_all_analyzers`` dispatches every analyzer into one ThreadPoolExecutor,
and ``run_all_linkers`` dispatches each linker priority cohort into another.
A pass's wall time therefore includes every moment its worker spent waiting
for the GIL while a sibling ran Python bytecode, so:

1. cards OVERLAP: the 96 cards of one measured survey summed to 157.7 s
   inside a 93.9 s wall span, and any "share of wall" computed from card sums
   is inflated;
2. a card INFLATES WITH CONTENTION: the same 43 bash files billed 2.3 s
   beside a one-file python analysis and 49.9 s beside a 1121-file one. The
   bash analyzer did identical work both times.

``cpu_ms`` is the CPU time consumed by the thread that ran the pass
(``time.thread_time_ns``). Waiting on the GIL, on I/O or on a sleep does not
advance it, so it measures what the pass cost rather than the machine's load,
and ``duration_ms - cpu_ms`` is how long the pass waited. Both are recorded:
wall stays what the schema has always called it, and the difference is what
lets a reader SEE contention.

What ``cpu_ms`` does not count
------------------------------
It is the CPU of ONE thread. It excludes CPU spent in child processes a pass
launches (the scip-python and rust-analyzer backends run external indexers)
and in any threads a pass starts itself. For those passes it is a lower
bound on the pass's cost, not the cost.

How it is wired
---------------
``thread_time`` is per-thread, so a clock must be started AND read on the
thread that runs the pass. ``PassClock`` records the thread it was started on
and refuses to be read anywhere else: reading another thread's counter would
return a plausible number that measures nothing, which is worse than an
error. The orchestrators start the clock inside the worker (the analyzer
pool's ``_run_costed`` wrapper, the linker chokepoint
``_run_linker_with_cache``) and hand the frozen ``PassCost`` back to the
thread that serializes the run. ``PassCost.stamp`` writes BOTH fields, so a
pass body's own ``duration_ms`` is replaced by the orchestrator's reading of
the whole call: a body timer covers only part of the pass, and a wall figure
over a different span from the CPU figure cannot be subtracted from it. The
serial synthesis passes (file-symbol
synthesis, producer merge, route materialization, CBV expansion, enclosure,
boundary synthesis) run on the orchestrating thread with no pool alive, and
use the same clock directly.

Rounding
--------
``wall_ms`` truncates, matching how every pass body has always computed
``duration_ms``. ``cpu_ms`` rounds UP, and is at least 1: a measured pass
executed code, and a 0 would read as "cost nothing". The bias is under 1 ms
per pass on a clock with sub-millisecond resolution. A run that
nobody measured carries ``cpu_ms=None`` (serialized ``null``): the field
cannot see, which is a different statement from a measured zero.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .ir import AnalysisRun

_NS_PER_MS = 1_000_000


@dataclass(frozen=True)
class PassCost:
    """One pass's measured cost, frozen on the thread that ran it."""

    wall_ms: int
    cpu_ms: int

    def stamp(self, run: "AnalysisRun") -> None:
        """Record this cost on ``run``: BOTH fields, from one clock.

        ``duration_ms`` is overwritten even when the pass body timed itself.
        A body's timer covers a SUB-span of the pass (the python analyzer
        stops its clock before parsing pyproject dependencies, and work done
        before a body starts its timer is outside it too), so keeping it
        beside a ``cpu_ms`` read over the whole call made the two disagree
        about what they measure: measured on this repository, the python
        card read ``duration_ms=663`` beside ``cpu_ms=1086``, which no single
        span can produce. With both from one span, ``duration_ms - cpu_ms``
        is the time the pass waited.
        """
        run.duration_ms = self.wall_ms
        run.cpu_ms = self.cpu_ms


class PassClock:
    """Start on the thread that runs the pass; ``read()`` on that thread."""

    __slots__ = ("_cpu0", "_thread", "_wall0")

    def __init__(self) -> None:
        self._thread = threading.get_ident()
        self._wall0 = time.perf_counter_ns()
        self._cpu0 = time.thread_time_ns()

    def read(self) -> PassCost:
        """Return the wall and own-thread CPU time elapsed since start."""
        if threading.get_ident() != self._thread:
            raise RuntimeError(
                "PassClock read on a different thread from the one it was "
                "started on: thread_time is per-thread, so the reading would "
                "be another thread's CPU"
            )
        wall_ns = time.perf_counter_ns() - self._wall0
        cpu_ns = time.thread_time_ns() - self._cpu0
        return PassCost(
            wall_ms=wall_ns // _NS_PER_MS,
            # Ceiling, floored at 1: a coarse per-thread clock (Windows ticks
            # in ~15.6 ms steps) can read 0 ns for a pass that ran.
            cpu_ms=max(1, -(-cpu_ns // _NS_PER_MS)),
        )
