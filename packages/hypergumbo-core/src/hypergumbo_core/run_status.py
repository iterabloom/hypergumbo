# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run status for humans watching a long run: a phase line, a heartbeat, a summary.

What it replaces
----------------
``run_survey`` and the sketch each kept their own progress reporter, and both
printed ``[ 55%] Running linkers... ETA 349s``. The percentage was a constant
written next to each phase marker, and the ETA was ``elapsed / pct``, so it
was only as good as a guess made when the phase table was written. On a
cold self-run with two cores the survey spent most of its time under one
label while the ETA counted down past zero. The two reporters also each printed
their own ``Complete in``: ``hypergumbo <path>`` runs the survey nested inside
the sketch, so a run ended with two completion lines (856.6s, then 1702.1s)
and nothing at all printed for the 845 s between them.

What it does
------------
One process-global ``RunStatus`` that both pipelines report into:

* **Phase line.** ``phase("Running linkers")`` redraws one ``\\r`` line with the
  run's elapsed time and the phase's own elapsed time. It does not show a
  percentage or an ETA: neither had a basis.
  ``item(label, current, total)`` appends a real count when a phase has
  one (files embedded, files analyzed).
* **Heartbeat.** Every ``HG_HEARTBEAT`` seconds (default 30) a daemon thread
  prints permanent lines. The first holds the phase, its elapsed time, and
  an abridged stack of the thread that started the run: its innermost
  hypergumbo frames, outermost first (``main: cli.run_survey >
  registry.run_all_linkers > cgo.link_cgo:212``). Then one line per busy
  pool worker: the pass it is running and its innermost frame
  (``bash  bash.find_bash_files:235``). The worker lines matter because
  analyzers and linkers run in ``ThreadPoolExecutor``s, and while they run
  the main thread only waits on futures. They were first one comma-joined
  tally on the main line, and six workers made it unreadable: ``, `` between
  workers and `` > `` inside each, a leading ``…`` on every one, and no pass
  name, so generic base-class code (``base.TreeSitterAnalyzer._analyze_body``)
  could not say which language it was analyzing. The pass name comes from
  ``worker_pass``, which the analyzer and linker pools enter around each
  pass; a thread outside both is named by the module of its outermost
  hypergumbo frame. The lines are permanent so the scrollback is a timeline
  of where the run spent its time, which a ``\\r`` line that keeps
  overwriting itself cannot give.
* **Summary.** When the outermost run ends: ``Complete in 28m22s``, and for
  a run of 30 s or more the slowest phases, so "what was slow" is answered
  without re-running under a profiler.
* **Clean interleaving.** On a live run ``sys.stderr`` is wrapped so that any
  other write (a ``UserWarning``, a ``print(..., file=sys.stderr)``) first
  clears the phase line. Before, a warning landed mid-line:
  ``[ 55%] Running linkers... ETA 349s    /usr/.../cli.py:12846: UserWarning``.

Why sample stacks instead of instrumenting
------------------------------------------
The pipeline has well over a hundred passes and dozens of non-pass phases.
Naming every one of them by hand would rot, and per-call hooks such as
``sys.setprofile`` cost time in the hot analyzer loops. ``sys._current_frames()``
returns every thread's current frame in one call. Walking a few dozen
``f_back`` links once per interval costs microseconds every 30 seconds and
adds nothing to the code being observed. A sample shows where the run is at
that instant, not how long each part took, but at a 30 s interval the
timeline is fine-grained enough to locate a part that takes minutes.

When it is active
-----------------
A run is *live* when the caller asked for progress and the stream is a TTY:
captured stderr never gets ``\\r`` redraws (WI-vuhas). The heartbeat runs on
a live run, and also on a non-TTY run when ``HG_HEARTBEAT`` is set
explicitly, for logs and agent harnesses that want the timeline without the
redraws. ``HG_HEARTBEAT=0`` disables the heartbeat.

Nesting
-------
``begin``/``end`` keep a depth count, so the survey nested inside a sketch
joins the sketch's run instead of starting its own clock: one timeline and
one ``Complete`` line. ``reports_run`` decorates a pipeline entry point and
calls ``end`` in a ``finally``, so a run that raises leaves no depth behind
for the next run in the same process.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import os
import sys
import threading
import time
from typing import IO, Any, Callable, Iterator, Optional, TypeVar

_F = TypeVar("_F", bound=Callable[..., Any])

DEFAULT_HEARTBEAT_SECONDS = 30.0
_ENV_HEARTBEAT = "HG_HEARTBEAT"
_MAIN_FRAMES = 4
_MAX_WORKER_LINES = 8
_WORKER_INDENT = " " * 11
_SUMMARY_PHASES = 3
_SUMMARY_MIN_SECONDS = DEFAULT_HEARTBEAT_SECONDS


def format_duration(seconds: float) -> str:
    """``42s`` / ``3m12s`` / ``1h02m``: short enough for a status line."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def _frame_label(frame: Any) -> Optional[str]:
    """``module_tail.qualname`` for a hypergumbo frame, None for any other.

    Frames are kept by their module's ``__name__``, so stdlib, threading,
    concurrent.futures and third-party frames drop out. Decorator
    ``wrapper`` frames drop out too: they say nothing about where the run
    is. What is left is the part of the stack a hypergumbo reader can act on.
    """
    module = frame.f_globals.get("__name__", "")
    if not module.startswith("hypergumbo") or module == __name__:
        return None
    code = frame.f_code
    qualname = getattr(code, "co_qualname", code.co_name)
    if qualname.endswith(".wrapper"):
        return None
    return f"{module.rsplit('.', 1)[-1]}.{qualname.replace('<locals>.', '')}"


def _collapsed_frames(frame: Any) -> tuple[list[list[Any]], int]:
    """A thread's hypergumbo frames as ``[label, depth]``, outermost first.

    Consecutive frames with one label (recursion) collapse into one entry
    whose depth counts them. Also returns the innermost frame's line number.
    """
    labels: list[str] = []
    innermost_line = 0
    while frame is not None:
        label = _frame_label(frame)
        if label is not None:
            if not labels:
                innermost_line = frame.f_lineno
            labels.append(label)
        frame = frame.f_back
    collapsed: list[list[Any]] = []
    for label in reversed(labels):
        if collapsed and collapsed[-1][0] == label:
            collapsed[-1][1] += 1
        else:
            collapsed.append([label, 1])
    return collapsed, innermost_line


def abridged_stack(frame: Any, max_frames: int, elide: bool = True) -> str:
    """The innermost ``max_frames`` hypergumbo frames, outermost first.

    The innermost frame carries its line number, which is what places a
    sample inside a function hundreds of lines long (``run_survey``).
    Recursion shows after it as ``(recursion xN)``; a bare ``xN`` ran into
    the line number (``x2:1166``). A cut-off stack starts with ``…`` unless
    ``elide`` is False. An empty string means the thread had no hypergumbo
    frame, which is an idle pool worker.
    """
    collapsed, innermost_line = _collapsed_frames(frame)
    if not collapsed:
        return ""
    parts = []
    for i, (label, depth) in enumerate(collapsed):
        text = f"{label}:{innermost_line}" if i == len(collapsed) - 1 else label
        parts.append(f"{text} (recursion x{depth})" if depth > 1 else text)
    if len(parts) > max_frames:
        parts = (["…"] if elide else []) + parts[-max_frames:]
    return " > ".join(parts)


_WORKER_PASSES: dict[int, str] = {}


@contextlib.contextmanager
def worker_pass(name: str) -> Iterator[None]:
    """Name the pass the current thread runs, for the heartbeat's worker lines.

    One dict store and one pop per pass, on the thread that runs it; the
    heartbeat thread only reads. CPython makes single dict operations
    atomic, so no lock is needed.
    """
    ident = threading.get_ident()
    previous = _WORKER_PASSES.get(ident)
    _WORKER_PASSES[ident] = name
    try:
        yield
    finally:
        if previous is None:
            _WORKER_PASSES.pop(ident, None)
        else:
            _WORKER_PASSES[ident] = previous


def _thread_name(ident: int, frame: Any) -> str:
    """The pass a thread runs, else the module of its outermost hypergumbo frame."""
    name = _WORKER_PASSES.get(ident)
    if name is not None:
        return name
    collapsed, _ = _collapsed_frames(frame)
    return str(collapsed[0][0]).split(".", 1)[0]


def _heartbeat_interval() -> tuple[float, bool]:
    """The interval and whether ``HG_HEARTBEAT`` set it explicitly.

    An unparseable value falls back to the default and is not treated as
    explicit, so a typo does not turn heartbeats on for a captured run.
    """
    raw = os.environ.get(_ENV_HEARTBEAT)
    if raw is None:
        return DEFAULT_HEARTBEAT_SECONDS, False
    try:
        return max(0.0, float(raw)), True
    except ValueError:
        return DEFAULT_HEARTBEAT_SECONDS, False


class _LineClearingStream:
    """``sys.stderr`` during a live run: other writers clear the phase line first.

    Everything not overridden is delegated, so ``isatty``, ``fileno`` and
    ``encoding`` answer for the real stream.
    """

    def __init__(self, status: "RunStatus", inner: IO[str]) -> None:
        self._status = status
        self._inner = inner

    def write(self, text: str) -> int:
        with self._status._lock:
            self._status._clear_drawn()
            return self._inner.write(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class RunStatus:
    """The process-wide status of the outermost pipeline run.

    All writes go through one lock, because the heartbeat thread and the
    pipeline thread both write to the same line.
    """

    def __init__(self, stream: Optional[IO[str]] = None) -> None:
        self._stream_override = stream
        self._lock = threading.RLock()
        self._depth = 0
        self._live = False
        self._stream: IO[str] = sys.stderr
        self._start = 0.0
        self._owner_ident = 0
        self._phases: list[tuple[str, float]] = []
        self._detail = ""
        self._drawn = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ---------------------------------------------------------

    def begin(self, progress: bool) -> bool:
        """Join the current run, or start one. Returns a token for ``end``.

        A nested call joins whatever run is active, so the depth stays
        balanced however the caller set ``progress``. At depth 0 a run
        starts only if it will show something: a live phase line, or an
        explicitly requested heartbeat.
        """
        with self._lock:
            if self._depth > 0:
                self._depth += 1
                return True
            if not progress:
                return False
            stream = self._stream_override or sys.stderr
            interval, explicit = _heartbeat_interval()
            live = bool(getattr(stream, "isatty", lambda: False)())
            if not live and not explicit:
                return False
            self._depth = 1
            self._stream = stream
            self._live = live
            self._start = time.monotonic()
            self._owner_ident = threading.get_ident()
            self._phases = []
            self._detail = ""
            self._drawn = 0
            if live and stream is sys.stderr:
                sys.stderr = _LineClearingStream(self, stream)
            if interval > 0:
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._beat, args=(interval,),
                    name="hypergumbo-heartbeat", daemon=True,
                )
                self._thread.start()
            return True

    def end(self, token: bool) -> None:
        """Leave the run; the outermost ``end`` prints the summary."""
        if not token:
            return
        with self._lock:
            self._depth -= 1
            if self._depth > 0:
                return
            thread, self._thread = self._thread, None
            self._stop.set()
        if thread is not None:
            thread.join(timeout=5)
        with self._lock:
            if isinstance(sys.stderr, _LineClearingStream):
                sys.stderr = sys.stderr._inner
            elapsed = time.monotonic() - self._start
            self._write_line(f"Complete in {format_duration(elapsed)}")
            summary = self.slowest_phases(time.monotonic())
            if summary and elapsed >= _SUMMARY_MIN_SECONDS:
                self._write_line(f"Slowest phases: {summary}")

    # -- reporting ---------------------------------------------------------

    def phase(self, label: str) -> None:
        """Enter a new phase. A repeat of the current label is ignored."""
        with self._lock:
            if self._depth == 0:
                return
            if not self._phases or self._phases[-1][0] != label:
                self._phases.append((label, time.monotonic()))
            self._detail = ""
            self._redraw()

    def item(self, label: str, current: int, total: int) -> None:
        """Attach a real count to the current phase (``Embedding files 41/53``)."""
        with self._lock:
            if self._depth == 0:
                return
            self._detail = f"{label} {current}/{total}"
            self._redraw()

    # -- rendering ---------------------------------------------------------

    def _status_text(self, now: float) -> str:
        text = f"[{format_duration(now - self._start):>6}]"
        if self._phases:
            label, since = self._phases[-1]
            text += f" {label} ({format_duration(now - since)})"
        if self._detail:
            text += f" · {self._detail}"
        return text

    def slowest_phases(self, now: float) -> str:
        """The longest phases, by total time spent under each label."""
        totals: dict[str, float] = {}
        bounds = [since for _, since in self._phases[1:]] + [now]
        for (label, since), until in zip(self._phases, bounds, strict=True):
            totals[label] = totals.get(label, 0.0) + until - since
        longest = sorted(totals.items(), key=lambda kv: -kv[1])[:_SUMMARY_PHASES]
        return ", ".join(f"{label} {format_duration(secs)}" for label, secs in longest)

    def heartbeat_line(self, now: float) -> str:
        """The status line, then one indented line per busy pool worker.

        The status line carries the run thread's abridged stack after
        ``main``, named by its pass when it is running one itself (a serial
        linker). Workers are sorted by pass name and capped at
        ``_MAX_WORKER_LINES``, the rest folded into ``+N more``.
        """
        frames = sys._current_frames()
        line = self._status_text(now)
        owner = frames.get(self._owner_ident)
        if owner is not None:
            stack = abridged_stack(owner, _MAIN_FRAMES)
            if stack:
                main = _WORKER_PASSES.get(self._owner_ident)
                line += f" | main ({main}): {stack}" if main else f" | main: {stack}"
        me = threading.get_ident()
        workers: list[tuple[str, str]] = []
        for ident, frame in frames.items():
            if ident in (self._owner_ident, me):
                continue
            stack = abridged_stack(frame, 1, elide=False)
            if stack:
                workers.append((_thread_name(ident, frame), stack))
        workers.sort()
        shown = workers[:_MAX_WORKER_LINES]
        width = max((len(name) for name, _ in shown), default=0)
        lines = [line] + [
            f"{_WORKER_INDENT}{name:<{width}}  {stack}" for name, stack in shown
        ]
        if len(workers) > len(shown):
            lines.append(f"{_WORKER_INDENT}+{len(workers) - len(shown)} more")
        return "\n".join(lines)

    def _redraw(self) -> None:
        if not self._live:
            return
        text = self._status_text(time.monotonic())
        pad = " " * max(0, self._drawn - len(text))
        self._stream.write(f"\r{text}{pad}")
        self._stream.flush()
        self._drawn = len(text)

    def _clear_drawn(self) -> None:
        if self._drawn:
            self._stream.write("\r" + " " * self._drawn + "\r")
            self._drawn = 0

    def _write_line(self, text: str) -> None:
        """A permanent line: clear the phase line, write, then redraw it."""
        if self._live:
            first, _, rest = text.partition("\n")
            pad = " " * max(0, self._drawn - len(first))
            tail = f"\n{rest}" if rest else ""
            self._stream.write(f"\r{first}{pad}{tail}\n")
            self._drawn = 0
        else:
            self._stream.write(f"{text}\n")
        self._stream.flush()

    def _beat(self, interval: float) -> None:
        while not self._stop.wait(interval):
            with self._lock:
                if self._depth == 0:  # pragma: no cover - end() races the wait
                    return
                self._write_line(self.heartbeat_line(time.monotonic()))
                self._redraw()


STATUS = RunStatus()


def begin(progress: bool) -> bool:
    """Module-level ``RunStatus.begin`` on the process-wide status."""
    return STATUS.begin(progress)


def end(token: bool) -> None:
    """Module-level ``RunStatus.end`` on the process-wide status."""
    STATUS.end(token)


def phase(label: str) -> None:
    """Module-level ``RunStatus.phase`` on the process-wide status."""
    STATUS.phase(label)


def item(label: str, current: int, total: int) -> None:
    """Module-level ``RunStatus.item`` on the process-wide status."""
    STATUS.item(label, current, total)


def reports_run(fn: _F) -> _F:
    """Bracket a pipeline entry point in ``begin``/``end``, ``end`` in a ``finally``.

    The wrapped function must take a ``progress`` parameter; its value, from
    the call or the default, decides whether a run starts at depth 0.
    """
    sig = inspect.signature(fn)
    default = sig.parameters["progress"].default

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        progress = sig.bind_partial(*args, **kwargs).arguments.get(
            "progress", default,
        )
        token = begin(bool(progress))
        try:
            return fn(*args, **kwargs)
        finally:
            end(token)

    return wrapper  # type: ignore[return-value]
