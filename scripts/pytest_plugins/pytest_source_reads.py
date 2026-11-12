# SPDX-License-Identifier: AGPL-3.0-or-later
"""pytest plugin: record how each test can see the TEXT of a repository ``.py`` file.

WHY. A change that touches only comments and docstrings cannot alter what a
test executes. It can alter a test's outcome only where the test (or code it
calls) sees the changed file's text, a docstring, or a line number. smart-test's
prose-only rule (``hypergumbo_core.prose_only``) runs the tests recorded doing
so and drops the rest. This plugin produces that record.

WHAT IT RECORDS, per pytest process, into ``--source-reads-dir``:

* READS: every ``open`` of a ``.py`` file under the rootdir that is not the
  import system loading a module, and every ``linecache.getlines`` call for
  one (``inspect.getsource``, a formatted traceback), attributed to what was
  running. linecache is recorded at its API because it serves later callers
  from a cache without opening anything.
* SPAWNS: every process start (``subprocess``, ``os.system``, ``fork``,
  ``exec``). A child's reads are invisible here, so a test that spawns one is
  OPAQUE and the rule keeps it. One exception is decided here: ``git`` run
  somewhere else (its directory, arguments and ``GIT_*`` variables all point
  outside the rootdir) cannot read a repository file and is not recorded.
* DOCSTRINGS AND LINE NUMBERS READ AT RUNTIME (Python 3.12+, via
  ``sys.monitoring``): when a line holding ``x.__doc__``, ``frame.f_lineno``
  and the like runs, ``x`` is evaluated in that frame and the file (and for a
  docstring, the qualified name) it belongs to is recorded. A site whose
  subject cannot be evaluated, or that reads docstrings or line numbers
  wholesale (``inspect.getdoc``, ``traceback.extract_stack``), is recorded as
  ``*``: it may have seen any of them.
* the test ids each test file collects, so that a file with a test that never
  ran (a run cut short) is not mistaken for a file with complete evidence.

ATTRIBUTION. An event belongs to the test running at the time. It also belongs
to each fixture being set up at the time (other than function scope): a module
or session fixture runs once and its value is reused, so every test that uses
the fixture inherits its events, across xdist workers too (the consumer unions
by fixture key). An event during a test module's import belongs to that module.
An event under the BODY of any other repository module (a conftest, or a source
module that scans files at import) is UNATTRIBUTED whichever test triggered the
import, because the module runs once per process and keeps the result; so is
one made by repository code outside every test. The consumer treats such a read
as made by every test, and an unattributed spawn as a reason not to act at all.

WHAT IS NOT RECORDED.

* An ``open`` whose two innermost Python frames are frozen importlib/runpy code
  (``get_data`` called by ``get_code``): that is an import.
* An ``open`` or spawn that pytest or one of its plugins makes for itself:
  walking outward from the event, pytest's code is reached before any
  repository code (pytest reads a test module to find line numbers; coverage
  reads the sources it reports on).
* A read made by a function named with ``--source-reads-hash-only
  MODULE:FUNCTION``. smart-test names ``hypergumbo_core.ir:compute_pass_version``,
  which reads a pass module's source and returns only its SHA-256: the text
  reaches nothing else, and the hash of a module cannot differ between the two
  sides of any comparison made inside one test run.

NOT SEEN: a value cached in a module global by one test and used by another
(neither a fixture, nor a module body, nor ``linecache``); a docstring or line
number read by third-party code (a CLI library building help text from
``__doc__``) or through an attribute named plain ``lineno`` (a ``LogRecord``,
a ``FrameSummary``). The consumer's docstring states these limits.

Use: ``PYTHONPATH=scripts/pytest_plugins pytest -p pytest_source_reads
--source-reads-dir=DIR``. Off unless the option is given. An audit hook cannot
be removed, so one is installed when this module is imported -- before any
conftest -- and it buffers until the option is known.
"""
from __future__ import annotations

import ast
import json
import linecache
import os
import re
import sys
import threading
import types
from typing import Any, Optional

import pytest

#: Bumped whenever what a record means changes. The consumer discards evidence
#: recorded under any other version.
VERSION = 2

#: Audit events that start a process whose file reads this plugin cannot see.
SPAWN_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    "os.fork", "os.forkpty", "pty.spawn",
})

#: pytest's own modules. Walking outward from an event, reaching one of these
#: before any repository code means pytest (or a plugin it called) did it.
_PYTEST = ("_pytest.", "pluggy.")

READ, CACHED_READ, DOC, LINE = "open", "linecache", "doc", "line"

#: "It may have seen any of them."
ANY = "*"


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--source-reads-dir", default=None, metavar="DIR",
        help="record per-test reads of repository .py files into DIR",
    )
    parser.addoption(
        "--source-reads-hash-only", action="append", default=[],
        metavar="MODULE:FUNCTION",
        help="a function whose reads of source text are not recorded, because "
             "it returns only a digest of the text",
    )


class _Bucket:
    __slots__ = ("docs", "lines", "opaque", "reads")

    def __init__(self) -> None:
        self.reads: set[str] = set()
        self.opaque: set[str] = set()
        self.docs: set[str] = set()
        self.lines: set[str] = set()

    def dump(self) -> dict[str, list[str]]:
        return {"reads": sorted(self.reads), "opaque": sorted(self.opaque),
                "docs": sorted(self.docs), "lines": sorted(self.lines)}


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


# ── Sites that read a docstring or a line number at runtime ─────────────────

_DOC_READERS = frozenset({"getdoc", "pydoc"})
_LINE_ATTRS = frozenset({
    "f_lineno", "tb_lineno", "co_firstlineno", "co_lines", "co_positions",
})
_LINE_READERS = frozenset({
    "getframeinfo", "extract_stack", "extract_tb", "walk_stack", "walk_tb",
    "getouterframes", "getinnerframes", "format_stack", "StackSummary",
})
#: Cheap test before parsing: a file with none of these words has no site.
_ANY_SITE = re.compile("|".join(sorted(
    {"__doc__", "help", "%\\(lineno\\)", *_DOC_READERS, *_LINE_ATTRS,
     *_LINE_READERS})))
#: The file's own docstrings: a bare ``__doc__`` name.
_OWN = "<own>"


def _simple(node: ast.AST) -> bool:
    """An expression safe to evaluate a second time: a name, or attributes
    of one."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name)


def sites(source: str) -> dict[int, list[tuple[str, Optional[str]]]]:
    """Lines of ``source`` that read a docstring or a line number.

    Maps a line number to ``(kind, subject)`` pairs: kind is DOC or LINE, and
    subject is the source of the expression whose docstring or line number is
    read, ``_OWN`` for the file's own, or None when it cannot be evaluated
    (the site then counts as reading ANY). Import statements are not sites:
    importing ``getdoc`` reads nothing.
    """
    if not _ANY_SITE.search(source):
        return {}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    found: dict[int, list[tuple[str, Optional[str]]]] = {}

    def add(node: ast.AST, kind: str, subject: Optional[str]) -> None:
        # The attribute name of a multi-line expression can sit on its last
        # line, and the instruction is numbered from there.
        for lineno in {node.lineno, getattr(node, "end_lineno", None) or node.lineno}:
            found.setdefault(lineno, []).append((kind, subject))

    def subject_of(node: ast.Attribute) -> Optional[str]:
        return ast.unparse(node.value) if _simple(node.value) else None

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            if node.attr == "__doc__":
                add(node, DOC, subject_of(node))
            elif node.attr in _LINE_ATTRS:
                add(node, LINE, subject_of(node))
            elif node.attr in _DOC_READERS:
                add(node, DOC, None)
            elif node.attr in _LINE_READERS:
                add(node, LINE, None)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id == "__doc__":
                add(node, DOC, _OWN)
            elif node.id in _DOC_READERS:
                add(node, DOC, None)
            elif node.id in _LINE_READERS:
                add(node, LINE, None)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == "__doc__":
                add(node, DOC, None)
            elif "%(lineno)" in node.value:
                add(node, LINE, None)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "help"):
            add(node, DOC, None)
    return found


def _doc_owner(obj: Any) -> Optional[tuple[str, str]]:
    """``(file, qualified name)`` of the definition whose docstring
    ``obj.__doc__`` is; None when it has no file (a builtin)."""
    if isinstance(obj, types.ModuleType):
        return (getattr(obj, "__file__", None) or "", "<module>")
    if isinstance(obj, property):
        obj = obj.fget
    obj = getattr(obj, "__func__", obj)
    if not hasattr(obj, "__qualname__"):
        obj = type(obj)
    module = sys.modules.get(getattr(obj, "__module__", None) or "")
    return (getattr(module, "__file__", None) or "", obj.__qualname__)


def _line_owner(obj: Any) -> str:
    """The file whose line numbers ``obj`` (a frame, traceback, code object
    or function) carries."""
    obj = getattr(obj, "tb_frame", obj)
    obj = getattr(obj, "f_code", obj)
    obj = getattr(obj, "__code__", obj)
    return str(obj.co_filename)


class SourceReads:
    def __init__(self, out_dir: str, root: str, hash_only: list[str]) -> None:
        self.out_dir = out_dir
        self.root = os.path.realpath(root) + os.sep
        self.prefix = os.path.realpath(sys.prefix) + os.sep
        self.hash_only = {tuple(item.split(":", 1)) for item in hash_only}
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
        if _STATE["root"] != self.root and _STATE["watching"]:
            # Files were judged against the working directory; judge again.
            _STATE["root"] = self.root
            _SITES.clear()
            sys.monitoring.restart_events()

    # -- the audit hook ----------------------------------------------------
    def _is_repo_code(self, filename: str) -> bool:
        return (filename.startswith(self.root)
                and not filename.startswith(self.prefix)
                and "site-packages" not in filename)

    def _targets(self, event: str, stack: list[tuple[str, str, str]]) -> list[_Bucket]:
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
            # linecache opens a file once and then serves its copy; the
            # getlines wrapper below records every caller instead.
            if event == READ and module == "linecache":
                return []
            if self._is_repo_code(filename):
                if (module, function) in self.hash_only:
                    return []
                if function == "<module>" and os.path.realpath(filename) != self.collecting:
                    return [self.unattributed]
                targets = list(self.scopes)
                if self.test is not None:
                    targets.append(self.test)
                # Repository code outside every test (a conftest hook).
                return targets or [self.unattributed]
            if module.startswith(_PYTEST):
                return []
        return []

    def _rel(self, path: str) -> Optional[str]:
        """``path`` relative to the rootdir; None when it is outside it."""
        real = os.path.realpath(path)
        if not real.startswith(self.root) or real.startswith(self.prefix):
            return None
        return real[len(self.root):]

    def audit(self, event: str, what: str, stack: list[tuple[str, str, str]]) -> None:
        if not self.active:
            return
        if event in (READ, CACHED_READ):
            rel = self._rel(what)
            if rel is not None:
                for bucket in self._targets(event, stack):
                    bucket.reads.add(rel)
        elif event in (DOC, LINE):
            # ``what`` is ANY, a file, or ``file::qualified name``.
            target = what
            if what != ANY:
                path, sep, name = what.partition("::")
                rel = self._rel(path) if path.endswith(".py") else None
                if rel is None:
                    return
                target = rel + sep + name
            for bucket in self._targets(event, stack):
                (bucket.docs if event == DOC else bucket.lines).add(target)
        else:
            for bucket in self._targets(event, stack):
                bucket.opaque.add(what)

    def git_runs_elsewhere(self, argv: Any, cwd: Any, env: Any) -> bool:
        """True when a ``git`` command cannot reach this repository: its
        working directory, every argument and every ``GIT_*`` variable name
        places outside the rootdir.

        git works on the repository it finds from its directory, ``-C``,
        ``--git-dir`` / ``--work-tree`` or the ``GIT_*`` variables, and on
        paths given as arguments, so a command with none of them inside the
        rootdir reads no file of it.
        """
        try:
            where = os.fsdecode(cwd) if cwd is not None else os.getcwd()
            words = [os.fsdecode(a) for a in argv][1:] if not isinstance(
                argv, (str, bytes)) else [ANY]
            environ = os.environ if env is None else env
            for key, value in environ.items():
                if os.fsdecode(key).startswith("GIT_"):
                    words.extend(os.fsdecode(value).split(os.pathsep))
        except (TypeError, ValueError, OSError):
            return False
        if self._inside(where, ""):
            return False
        for word in words:
            for piece in {word, word.partition("=")[2], word.removeprefix("file://")}:
                if piece and self._inside(where, piece):
                    return False
        return True

    def _inside(self, where: str, path: str) -> bool:
        real = os.path.realpath(os.path.join(where, path)) + os.sep
        return real.startswith(self.root)

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
        _watch_sites(False)
        os.makedirs(self.out_dir, exist_ok=True)
        payload = {
            "version": VERSION,
            "observed": bool(_STATE["observing"]),
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
#: ``root`` is the rootdir (the working directory until pytest names it).
_STATE: dict[str, Any] = {
    "recorder": None, "buffering": True, "observing": False, "watching": False,
    "root": os.path.realpath(os.getcwd()) + os.sep,
}
#: Set, per thread, while this plugin reads a file or evaluates a subject for
#: itself: what happens then is not the test's doing.
_OWN_WORK = threading.local()


def _busy() -> bool:
    return bool(getattr(_OWN_WORK, "busy", False))
_EARLY: list[tuple[str, str, list[tuple[str, str, str]]]] = []


def _route(event: str, what: str, frame: Any) -> None:
    recorder = _STATE["recorder"]
    if recorder is not None:
        recorder.audit(event, what, _stack(frame))
    elif _STATE["buffering"]:
        _EARLY.append((event, what, _stack(frame)))


def _hook(event: str, args: tuple[Any, ...]) -> None:
    """Reduce an audit event to ``open(<.py path>)`` or a spawn, and route it."""
    if event == "open":
        if _busy():
            return
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
            program = os.path.basename(os.fsdecode(args[0] or "?"))
            recorder = _STATE["recorder"]
            if (program == "git" and recorder is not None
                    and recorder.git_runs_elsewhere(args[1], args[2], args[3])):
                return
            what = f"{event}:{program}"
        frame = sys._getframe(1)
    else:
        return
    _route(event, what, frame)


sys.addaudithook(_hook)

_real_getlines = linecache.getlines


def _getlines(filename: str, module_globals: Any = None) -> list[str]:
    """``linecache.getlines``, recording the caller as a reader of the file."""
    if isinstance(filename, str) and filename.endswith(".py") and not _busy():
        _route(CACHED_READ, filename, sys._getframe(1))
    return _real_getlines(filename, module_globals)


linecache.getlines = _getlines


# ── Watching the sites (sys.monitoring, Python 3.12+) ───────────────────────

_SITES: dict[str, dict[int, list[tuple[str, Optional[str]]]]] = {}
_TOOL = 4
_SELF = os.path.realpath(__file__)


def _sites_of(filename: str) -> dict[int, list[tuple[str, Optional[str]]]]:
    """The sites of one repository file, read once. This plugin's own file,
    anything installed and anything unreadable have none."""
    known = _SITES.get(filename)
    if known is None:
        known = {}
        if (filename.endswith(".py") and filename.startswith(_STATE["root"])
                and "site-packages" not in filename
                and os.path.realpath(filename) != _SELF):
            _OWN_WORK.busy = True
            try:
                with open(filename, encoding="utf-8") as handle:
                    known = sites(handle.read())
            except (OSError, UnicodeDecodeError):
                pass
            finally:
                _OWN_WORK.busy = False
        _SITES[filename] = known
    return known


def _on_start(code: types.CodeType, _offset: int) -> Any:
    """First run of a code object: watch its lines if it holds a site."""
    known = _sites_of(code.co_filename)
    if known and any(line in known for _, _, line in code.co_lines()):
        sys.monitoring.set_local_events(_TOOL, code, sys.monitoring.events.LINE)
    return sys.monitoring.DISABLE


def _on_line(code: types.CodeType, lineno: int) -> Any:
    """A watched line is about to run: record what each site on it reads."""
    here = _SITES.get(code.co_filename, {}).get(lineno)
    if here is None:
        return sys.monitoring.DISABLE
    frame = sys._getframe(1)
    for kind, subject in here:
        what = ANY
        if subject == _OWN:
            what = f"{code.co_filename}::{ANY}"
        elif subject is not None:
            _OWN_WORK.busy = True
            try:
                value = eval(subject, frame.f_globals, frame.f_locals)  # noqa: S307
                if kind == DOC:
                    what = "::".join(_doc_owner(value))
                else:
                    what = _line_owner(value)
            except Exception:  # unevaluable: it read ANY
                what = ANY
            finally:
                _OWN_WORK.busy = False
        _route(kind, what, frame)
    return None


def _watch_sites(on: bool) -> None:
    """Start or stop watching. Without ``sys.monitoring`` (Python < 3.12),
    or with the tool slot taken, nothing is watched and the record says so."""
    monitoring = getattr(sys, "monitoring", None)
    if monitoring is None or on == _STATE["watching"]:
        return
    if on:
        try:
            monitoring.use_tool_id(_TOOL, "source-reads")
        except ValueError:
            return
        monitoring.register_callback(_TOOL, monitoring.events.PY_START, _on_start)
        monitoring.register_callback(_TOOL, monitoring.events.LINE, _on_line)
        monitoring.set_events(_TOOL, monitoring.events.PY_START)
        _STATE["observing"] = True
    else:
        monitoring.set_events(_TOOL, 0)
        monitoring.free_tool_id(_TOOL)
    _STATE["watching"] = on


_watch_sites(True)


def pytest_configure(config: Any) -> None:
    out_dir = config.getoption("--source-reads-dir")
    # An xdist controller runs no test; its own spawns (the workers) and
    # reads (the coverage report) would only be unattributed noise.
    distributing = bool(config.getoption("numprocesses", None)) \
        and not hasattr(config, "workerinput")
    if out_dir and not distributing:
        config.pluginmanager.register(
            SourceReads(out_dir, str(config.rootpath),
                        config.getoption("--source-reads-hash-only")),
            "source-reads")
    else:
        _STATE["buffering"] = False
        _EARLY.clear()
        _watch_sites(False)
        _STATE["observing"] = False
