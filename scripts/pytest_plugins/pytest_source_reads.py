# SPDX-License-Identifier: AGPL-3.0-or-later
"""pytest plugin: record which tests read a repository ``.py`` file as DATA.

WHY. A change that touches only comments and docstrings cannot alter what a
test executes. It can alter a test's outcome only where the test (or code it
calls) reads the changed file's text: a gate that scans sources, a test that
pins a line number, a generator that copies docstrings. smart-test's
prose-only rule (``hypergumbo_core.prose_only``) drops the other tests, and
may do so only on POSITIVE evidence that a test does not read the file. This
plugin produces that evidence.

WHAT IT RECORDS, per pytest process, into ``--source-reads-dir``:

* every ``open`` of a ``.py`` file under the rootdir that is not the import
  system loading a module, attributed to what was running;
* every process spawn (``subprocess``, ``os.system``, ``fork``, ``exec``).
  A child's reads are invisible here, so a test that spawns one is OPAQUE and
  the rule never drops it;
* the test ids each test file collects, so that a file with a test that never
  ran (a run cut short) is not mistaken for a file with complete evidence.

ATTRIBUTION. A read belongs to the test running at the time. It also belongs to
each fixture being set up at the time (other than function scope): a module or
session fixture runs once and its value is reused, so every test that uses the
fixture inherits the fixture's reads, across xdist workers too (the consumer
unions by fixture key). A read during a test module's import belongs to that
module. A read or spawn under the BODY of any other repository module (a
conftest, or a source module that scans files at import) is UNATTRIBUTED
whichever test triggered the import, because the module runs once per process
and keeps the result; so is one made by repository code outside every test.
The consumer treats a file read that way as read by every test, and an
unattributed spawn as a reason not to act at all.

WHAT IS NOT A READ. An ``open`` whose two innermost Python frames are frozen
importlib/runpy code (``get_data`` called by ``get_code``): that is an import.
And an ``open`` or spawn that pytest or one of its plugins makes for itself:
walking outward from the event, pytest's code is reached before any
repository code (pytest reads a test module to find line numbers; coverage
reads the sources it reports on).

NOT SEEN: a value cached in a module global by one test and used by another
(neither a fixture nor a read in the second test). The consumer's docstring
states this limit.

Use: ``PYTHONPATH=scripts/pytest_plugins pytest -p pytest_source_reads
--source-reads-dir=DIR``. Off unless the option is given. An audit hook cannot
be removed, so one is installed when this module is imported -- before any
conftest -- and it buffers until the option is known.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

import pytest

#: Audit events that start a process whose file reads this plugin cannot see.
SPAWN_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    "os.fork", "os.forkpty", "pty.spawn",
})

#: pytest's own modules. Walking outward from an event, reaching one of these
#: before any repository code means pytest (or a plugin it called) did it.
_PYTEST = ("_pytest.", "pluggy.")


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--source-reads-dir", default=None, metavar="DIR",
        help="record per-test reads of repository .py files into DIR",
    )


class _Bucket:
    __slots__ = ("opaque", "reads")

    def __init__(self) -> None:
        self.reads: set[str] = set()
        self.opaque: set[str] = set()

    def dump(self) -> dict[str, list[str]]:
        return {"reads": sorted(self.reads), "opaque": sorted(self.opaque)}


def _is_import(frame: Any) -> bool:
    """True when ``frame`` (the caller of ``open``) is the import system:
    ``get_data`` called by ``get_code``, both frozen."""
    outer = frame.f_back
    return (frame.f_code.co_filename.startswith("<frozen ")
            and outer is not None
            and outer.f_code.co_filename.startswith("<frozen "))


def _stack(frame: Any) -> list[tuple[str, str, str]]:
    """``(filename, module name, function name)`` per frame, innermost first."""
    out = []
    while frame is not None:
        out.append((frame.f_code.co_filename,
                    frame.f_globals.get("__name__", ""), frame.f_code.co_name))
        frame = frame.f_back
    return out


class SourceReads:
    def __init__(self, out_dir: str, root: str) -> None:
        self.out_dir = out_dir
        self.root = os.path.realpath(root) + os.sep
        self.prefix = os.path.realpath(sys.prefix) + os.sep
        self.collecting = ""
        self.active = True
        self.test: Optional[_Bucket] = None
        self.scopes: list[_Bucket] = []
        self.tests: dict[str, _Bucket] = {}
        self.test_fixtures: dict[str, set[str]] = {}
        self.fixtures: dict[str, _Bucket] = {}
        self.modules: dict[str, _Bucket] = {}
        self.unattributed = _Bucket()
        self.expected: dict[str, list[str]] = {}
        # Events from before this recorder existed (an initial conftest).
        for event, what, stack in _EARLY:
            self.audit(event, what, stack)
        _EARLY.clear()
        _STATE["recorder"] = self

    # -- the audit hook ----------------------------------------------------
    def _is_repo_code(self, filename: str) -> bool:
        return (filename.startswith(self.root)
                and not filename.startswith(self.prefix)
                and "site-packages" not in filename)

    def _targets(self, stack: list[tuple[str, str, str]]) -> list[_Bucket]:
        """The buckets an event with this stack belongs to; none if it is
        the test machinery's own.

        Walking outward, the first frame that is repository code or pytest
        decides. pytest first: pytest or a plugin did this for itself (it
        reads a test module's source to find line numbers; coverage reads
        what it reports on), and no test's outcome depends on it. This does
        not look at install paths, which differ between a virtualenv and a
        system Python.

        Repository code first, and it is a module BODY other than the test
        module being collected: the body runs once per process and keeps
        what it computes in its globals, whichever test happened to import
        it first, so the event is everyone's.
        """
        for filename, module, function in stack:
            if self._is_repo_code(filename):
                if (function == "<module>"
                        and os.path.realpath(filename) != self.collecting):
                    return [self.unattributed]
                targets = list(self.scopes)
                if self.test is not None:
                    targets.append(self.test)
                # Repository code outside every test (a conftest hook).
                return targets or [self.unattributed]
            if module.startswith(_PYTEST):
                return []
        return []

    def audit(self, event: str, what: str, stack: list[tuple[str, str, str]]) -> None:
        if not self.active:
            return
        if event == "open":
            real = os.path.realpath(what)
            if not real.startswith(self.root) or real.startswith(self.prefix):
                return
            rel = real[len(self.root):]
            for bucket in self._targets(stack):
                bucket.reads.add(rel)
        else:
            for bucket in self._targets(stack):
                bucket.opaque.add(what)

    # -- collection --------------------------------------------------------
    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(self, items: list[Any]) -> None:
        for item in items:
            self.expected.setdefault(item.nodeid.split("::", 1)[0], []).append(item.nodeid)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_make_collect_report(self, collector: Any) -> Any:
        if not isinstance(collector, pytest.Module):
            yield
            return
        bucket = self.modules.setdefault(collector.nodeid.split("::", 1)[0], _Bucket())
        self.scopes.append(bucket)
        self.collecting = os.path.realpath(str(collector.path))
        try:
            yield
        finally:
            self.collecting = ""
            self.scopes.pop()

    # -- fixtures and tests ------------------------------------------------
    @staticmethod
    def _fixture_key(fixturedef: Any) -> str:
        return f"{fixturedef.baseid}::{fixturedef.argname}"

    @pytest.hookimpl(hookwrapper=True)
    def pytest_fixture_setup(self, fixturedef: Any) -> Any:
        if fixturedef.scope == "function":
            yield
            return
        bucket = self.fixtures.setdefault(self._fixture_key(fixturedef), _Bucket())
        self.scopes.append(bucket)
        try:
            yield
        finally:
            self.scopes.pop()

    def pytest_runtest_logstart(self, nodeid: str) -> None:
        self.test = self.tests.setdefault(nodeid, _Bucket())

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_teardown(self, item: Any) -> Any:
        used = self.test_fixtures.setdefault(item.nodeid, set())
        info = getattr(item, "_fixtureinfo", None)
        for defs in getattr(info, "name2fixturedefs", {}).values():
            used.update(self._fixture_key(d) for d in defs if d.scope != "function")
        # Fixtures fetched with request.getfixturevalue() are not in the
        # static closure above; pytest keeps them on the request.
        request = getattr(item, "_request", None)
        for fixturedef in getattr(request, "_fixture_defs", {}).values():
            if fixturedef.scope != "function":
                used.add(self._fixture_key(fixturedef))
        yield

    def pytest_runtest_logfinish(self) -> None:
        self.test = None

    # -- output ------------------------------------------------------------
    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionfinish(self) -> None:
        # First, so that end-of-session work (reports) is not recorded.
        self.active = False
        os.makedirs(self.out_dir, exist_ok=True)
        payload = {
            "expected": self.expected,
            "tests": {
                nodeid: {**bucket.dump(),
                         "fixtures": sorted(self.test_fixtures.get(nodeid, ()))}
                for nodeid, bucket in self.tests.items()
            },
            "fixtures": {key: b.dump() for key, b in self.fixtures.items()},
            "modules": {key: b.dump() for key, b in self.modules.items()},
            "unattributed": self.unattributed.dump(),
        }
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
        target = os.path.join(self.out_dir, f"{worker}-{os.getpid()}.json")
        with open(target + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(target + ".tmp", target)


#: The audit hook is installed when this module is imported (``-p`` imports
#: it before any conftest) and cannot be removed. Until a recorder exists it
#: buffers; a run without the option never creates one and drops the buffer.
_STATE: dict[str, Any] = {"recorder": None, "buffering": True}
_EARLY: list[tuple[str, str, list[tuple[str, str, str]]]] = []


def _hook(event: str, args: tuple[Any, ...]) -> None:
    """Reduce an audit event to ``open(<.py path>)`` or a spawn, and route it."""
    if event == "open":
        path, mode = args[0], args[1]
        if isinstance(mode, str) and mode[:1] in "wxa" and "+" not in mode:
            return
        try:
            what = os.fsdecode(path)
        except TypeError:  # a file descriptor
            return
        if not what.endswith(".py"):
            return
        frame = sys._getframe(1)
        if _is_import(frame):
            return
    elif event in SPAWN_EVENTS:
        what = event
        if event == "subprocess.Popen":
            what = f"{event}:{os.path.basename(os.fsdecode(args[0] or '?'))}"
        frame = sys._getframe(1)
    else:
        return
    recorder = _STATE["recorder"]
    if recorder is not None:
        recorder.audit(event, what, _stack(frame))
    elif _STATE["buffering"]:
        _EARLY.append((event, what, _stack(frame)))


sys.addaudithook(_hook)


def pytest_configure(config: Any) -> None:
    out_dir = config.getoption("--source-reads-dir")
    # An xdist controller runs no test; its own spawns (the workers) and
    # reads (the coverage report) would only be unattributed noise.
    distributing = bool(config.getoption("numprocesses", None)) \
        and not hasattr(config, "workerinput")
    if out_dir and not distributing:
        config.pluginmanager.register(
            SourceReads(out_dir, str(config.rootpath)), "source-reads")
    else:
        _STATE["buffering"] = False
        _EARLY.clear()
