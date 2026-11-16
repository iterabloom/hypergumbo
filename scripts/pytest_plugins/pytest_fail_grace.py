# SPDX-License-Identifier: AGPL-3.0-or-later
"""pytest plugin: ``--fail-grace=N`` stops a run N seconds after its first failure.

WHY. A red run's verdict is decided at its first failure, and the per-PR
pytest step ran on to the end regardless. Measured on recent PRs (2026-11):
selections under about 150 files finish in 15 to 46 seconds, but selections
of 200 to 760 files take 11 to 28 minutes, and about one PR run in nine ends
red. pytest's own stops are counts (``-x``, ``--maxfail``): the first cuts off
failures that would have arrived a moment later, and the second waits
however long the Nth failure takes.

WHAT IT DOES. The first failed report starts a clock. Once N seconds have
passed, the session is told to stop; tests already running finish. Failures
that arrive inside the grace period are reported as usual, so one cause that
breaks several tests still shows all of them when they run close together.
An expected failure (xfail) is a skip to pytest and does not start the clock.

WHAT IT COSTS. The failure list can be cut short: an unrelated failure that
would have appeared later is not seen until the next run. The stop message
says so and names ``--fail-grace=0``, which turns the rule off.

TWO SWITCHES, BECAUSE THERE ARE TWO LOOPS. Without pytest-xdist the session
honours ``session.shouldfail``. Under xdist the controller's loop reads its
own ``DSession.shouldstop`` and ignores the session's flag until the run is
over (measured: all 60 tests ran). Both are set. xdist reports its stop as
"interrupted" (exit 2); the exit status is put back to 1, because tests
failed and nothing was interrupted from outside.

The clock is checked when a report arrives, so a stop can be late by the
length of the tests then running, never early.

Use: ``PYTHONPATH=scripts/pytest_plugins pytest -p pytest_fail_grace
--fail-grace=15``. Off unless the option is given with a positive value.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import pytest


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--fail-grace", type=float, default=None, metavar="SECONDS",
        help="stop the run this many seconds after the first failure (0 = never)",
    )


class FailGrace:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.first_failure: Optional[float] = None
        self.stopped = False
        self.session: Any = None

    def pytest_sessionstart(self, session: Any) -> None:
        self.session = session

    def pytest_runtest_logreport(self, report: Any) -> None:
        if self.stopped or self.session is None:
            return
        now = time.monotonic()
        if self.first_failure is None:
            if report.failed:
                self.first_failure = now
            return
        if now - self.first_failure < self.seconds:
            return
        self.stopped = True
        message = (
            f"fail-grace: stopped {self.seconds:g}s after the first failure; "
            "later tests did not run, so this failure list may be incomplete "
            "(--fail-grace=0 runs everything)"
        )
        self.session.shouldfail = message
        dsession = self.session.config.pluginmanager.get_plugin("dsession")
        if dsession is not None:
            dsession.shouldstop = message

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: Any) -> None:
        if self.stopped:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_configure(config: Any) -> None:
    seconds = config.getoption("--fail-grace")
    # The controller decides; an xdist worker only runs what it is sent.
    if seconds and seconds > 0 and not hasattr(config, "workerinput"):
        config.pluginmanager.register(FailGrace(seconds), "fail-grace")
