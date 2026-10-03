# SPDX-License-Identifier: AGPL-3.0-or-later
"""A cohort ledger: every multi-repo instrument states what it COVERED of what it was GIVEN.

THE DEFECT (WI-kovoj). A measurement over a cohort of repositories can lose a
member -- a full disk truncates a cached behavior map, an in-process CLI run
exits before writing its report, a path is mistyped -- and before this module
every instrument in ``scripts/`` that takes a cohort lost it SILENTLY, in one
of two shapes:

* DROPPED: ``except Exception: print(..., file=sys.stderr); continue``. The
  totals are computed over the survivors and the run exits 0. The only trace
  is a stderr line nobody reads.
* ZEROED: ``contextlib.suppress(SystemExit)`` around ``cli.main()``, then
  ``report = json.loads(...) if start >= 0 else {}``. A run that never wrote a
  report becomes an EMPTY report, and the member is scored as "0 findings" --
  indistinguishable from a repository that was analysed and had none.
  Reproduced on dev 62602239c6: ``measure-row-inflation.py CLAIMS
  flask-restful /nonexistent/kserve`` printed ``kserve ... EXCLUDED -- 0
  cross_function findings (0 findings)`` and exited 0.

Either way a shrunken denominator printed exactly like a full one. The case
that filed the item: a five-repo population reported as 73 blocked / 16
production sites, 23.5% of the true 137 / 68, because two repos were lost to
ENOSPC and a third was dropped -- and nothing in the result said so.

THE CURE IS A POSITIVE CLAIM, NOT A WARNING (LIVE.md section 5: absent is not
empty). An instrument builds a :class:`CohortLedger` from the members it was
given and runs each one inside :meth:`CohortLedger.attempt`. A member is
COVERED only when its block completes; an exception inside the block records it
as FAILED with the error, and the loop goes on to the next member; a member the
run never reached is NOT ATTEMPTED. The instrument then prints the claim
(``covered K of N given``, every uncovered member by name), writes it into its
JSON under ``"cohort"``, and returns :meth:`CohortLedger.exit_code`, which is
non-zero whenever K != N. A partial run is therefore visible in all three
places a reader or a calling agent looks: the text, the JSON and the exit code.

WHY A FAILED MEMBER DOES NOT ABORT THE RUN. Aborting is loud, but it invites the
operator to re-run with the failing member removed -- and THAT re-run is the
silent shrink, now with a clean exit code. Recording the failure and carrying
on keeps the given cohort as the denominator of record.

ABSENT OUTPUT RAISES. The readers here turn each way an output can be missing
into :class:`MemberAbsent` rather than an empty value:

* :func:`run_cli_json` -- an in-process ``hypergumbo`` subcommand whose stdout
  holds no JSON object. Its exit code is NOT the test: ``verify-claims`` exits
  1 on a violated claim, which is a verdict, not a failure. The report's
  presence is the positive signal.
* :func:`read_json_member` -- a behavior map that is missing, empty or cut off
  mid-write (the ENOSPC shape).
* :func:`require_dir` -- a repository path that is not a directory, which a
  file-walking scanner would otherwise read as "0 files".

DISK FULL IS NAMED. :func:`is_disk_full` walks the exception chain for
ENOSPC / EDQUOT so the claim says ``DISK FULL`` and the operator checks ``df``
before diagnosing a coverage gap or a code defect -- the item's second
observation was a ``check-package-coverage`` exit 1 that was a full /tmp.

``scripts/lib/`` is not a package: scripts put it on ``sys.path`` and import
this module by name, as they do ``pool_utils``. Standard library only;
``hypergumbo_core`` is imported lazily, by :func:`run_cli_json` alone.
"""

from __future__ import annotations

import contextlib
import errno
import io
import json
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, TextIO

#: Exit code for a run whose cohort is incomplete. Distinct from every code
#: an instrument here already uses: 1 (an error), 2 and 3 (a failed positive
#: control -- ``measure-ctor-root-typing-ab.py`` returns 3), and from
#: ``verify-claims``' verdict codes 0-3, which a calling agent may also be
#: reading.
COHORT_INCOMPLETE_EXIT = 4

#: errnos that mean the disk (or the quota) is full.
DISK_FULL_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT})

COVERED = "covered"
FAILED = "failed"
NOT_ATTEMPTED = "not_attempted"


class MemberAbsent(RuntimeError):
    """A cohort member's output is ABSENT -- which is not the same as empty."""


def is_disk_full(exc: BaseException) -> bool:
    """True when ``exc`` or anything it was raised from is ENOSPC / EDQUOT."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, OSError) and cur.errno in DISK_FULL_ERRNOS:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


class CohortLedger:
    """Given vs covered, member by member, for one instrument run."""

    def __init__(
        self,
        given: Iterable[str],
        *,
        unit: str = "repo",
        label: Callable[[str], str] | None = None,
    ) -> None:
        self._order: list[str] = []
        self._entries: dict[str, dict[str, Any]] = {}
        for member in given:
            if member in self._entries:
                raise ValueError(
                    f"cohort member {member!r} given twice; a cohort is a set "
                    f"and a duplicate would be counted twice in every total")
            self._order.append(member)
            self._entries[member] = {"member": member, "status": NOT_ATTEMPTED}
        self.unit = unit
        self._label = label or (lambda m: m)

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._order))

    def label(self, member: str) -> str:
        """The display name for ``member`` (identity stays the given string)."""
        return self._label(member)

    @contextlib.contextmanager
    def attempt(self, member: str) -> Iterator[None]:
        """Run one member; COVERED if the block completes, FAILED if it raises.

        Swallows :class:`Exception` (recorded, announced on stderr) so the run
        continues to the next member. ``BaseException`` -- KeyboardInterrupt,
        SystemExit -- propagates: the operator stopping the run is not a member
        failing.
        """
        if member not in self._entries:
            raise ValueError(f"{member!r} is not in the cohort this ledger was given")
        entry = self._entries[member]
        if entry["status"] != NOT_ATTEMPTED:
            raise ValueError(f"{member!r} was already attempted ({entry['status']})")
        try:
            yield
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            entry.update(status=FAILED, error=error, disk_full=is_disk_full(exc))
            hint = " [DISK FULL]" if entry["disk_full"] else ""
            print(f"!! {self.label(member)}: FAILED{hint} -- {error} "
                  f"(recorded in the cohort claim; continuing)", file=sys.stderr)
        else:
            entry["status"] = COVERED

    def _count(self, status: str) -> int:
        return sum(1 for e in self._entries.values() if e["status"] == status)

    @property
    def complete(self) -> bool:
        return self._count(COVERED) == len(self._order)

    def claim(self) -> dict[str, Any]:
        """The positive claim, JSON-ready: counts plus every member's status."""
        return {
            "unit": self.unit,
            "given": len(self._order),
            "covered": self._count(COVERED),
            "failed": self._count(FAILED),
            "not_attempted": self._count(NOT_ATTEMPTED),
            "complete": self.complete,
            "members": [dict(self._entries[m]) for m in self._order],
        }

    def report(self, stream: TextIO | None = None) -> None:
        """Print the claim. Call it LAST, after every number it qualifies."""
        out = stream if stream is not None else sys.stdout
        c = self.claim()
        head = f"COHORT: covered {c['covered']} of {c['given']} given {self.unit}(s)"
        if c["complete"]:
            print(f"{head} -- complete.", file=out)
            return
        print(f"{head} -- INCOMPLETE. Every total above is over {c['covered']}, "
              f"not {c['given']}; do not quote it as the cohort's.", file=out)
        for entry in c["members"]:
            name = self.label(entry["member"])
            if entry["status"] == FAILED:
                hint = ("  [DISK FULL -- check `df -h` before diagnosing anything else]"
                        if entry["disk_full"] else "")
                print(f"  FAILED        {name}: {entry['error']}{hint}", file=out)
            elif entry["status"] == NOT_ATTEMPTED:
                print(f"  NOT ATTEMPTED {name}", file=out)

    def exit_code(self, ok: int = 0) -> int:
        """``ok`` when the instrument already failed; else 0 iff complete."""
        if ok:
            return ok
        return 0 if self.complete else COHORT_INCOMPLETE_EXIT


def require_dir(path: str | Path) -> Path:
    """The resolved directory, or :class:`MemberAbsent` -- never "0 files"."""
    p = Path(path).resolve()
    if not p.is_dir():
        raise MemberAbsent(f"{path}: not a directory")
    return p


def read_json_member(path: str | Path) -> Any:
    """Parse a member's JSON file; missing, empty or truncated is ABSENT."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise MemberAbsent(f"{path}: unreadable ({exc})") from exc
    if not text.strip():
        raise MemberAbsent(f"{path}: empty file")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise MemberAbsent(
            f"{path}: not valid JSON ({exc}) -- truncated by a full disk?") from exc


class _Tee:
    """Write-through to ``stream`` while keeping a copy.

    stderr stays live for the operator (progress, warnings); the copy is what
    lets :func:`run_cli_json` see a disk-full error the CLI caught and printed.
    Anything else (``fileno``, ``isatty``, ``encoding``) is the real stream's:
    a plain class rather than an ``io.TextIOBase`` subclass, whose own
    ``fileno``/``isatty`` would shadow the delegation.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self.copy = io.StringIO()

    def write(self, text: str) -> int:
        self.copy.write(text)
        return self._stream.write(text)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


#: How a disk-full error reads once the CLI has caught it and printed it
#: (``hypergumbo: internal error: OSError: [Errno 28] No space left on
#: device`` -- observed live with stdout on /dev/full).
_DISK_FULL_TEXT = (
    f"[Errno {errno.ENOSPC}]", f"[Errno {errno.EDQUOT}]",
    "No space left on device", "Disk quota exceeded",
)


def run_cli_json(
    argv: list[str], *, main: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Run a ``hypergumbo`` subcommand in-process and return its JSON report.

    ``argv`` is the full ``sys.argv`` (``["hypergumbo", "verify-claims", ...]``).
    The first JSON object on stdout is the report; any banner before it and
    any text after it are ignored. NO REPORT RAISES :class:`MemberAbsent` with
    the exit code and the last thing written to stderr -- the exit code alone
    is not the test, because ``verify-claims`` exits 1 on a violated claim.

    ``cli.main`` catches unexpected exceptions, prints ``hypergumbo: internal
    error: ...`` (adding "This is a bug") and RETURNS 1, so an ENOSPC inside
    the run never reaches this frame as an exception. stderr is therefore
    teed, and when it carries a disk-full errno the :class:`MemberAbsent` is
    raised FROM an ``OSError(ENOSPC)`` so :func:`is_disk_full` -- and the
    cohort claim -- name the disk rather than a bug.
    """
    if main is None:
        from hypergumbo_core.cli import main as cli_main

        main = cli_main
    saved = sys.argv
    sys.argv = list(argv)
    buf = io.StringIO()
    err = _Tee(sys.stderr)
    code: Any = 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                # ``cli.main`` RETURNS its exit code (``__main__`` raises it).
                ret = main()
                code = 0 if ret is None else ret
            except SystemExit as exc:
                code = exc.code
    finally:
        sys.argv = saved
    raw = buf.getvalue()
    start = raw.find("{")
    if start >= 0:
        try:
            report, _ = json.JSONDecoder().raw_decode(raw[start:])
        except json.JSONDecodeError:
            report = None
        if isinstance(report, dict):
            return report
    err_text = err.copy.getvalue()
    err_lines = [ln for ln in err_text.splitlines() if ln.strip()]
    absent = MemberAbsent(
        f"`{' '.join(argv[1:3])}` wrote no JSON report (exit {code}); "
        f"stdout began: {raw.strip().splitlines()[:3]!r}; "
        f"stderr ended: {err_lines[-2:]!r}")
    if any(marker in err_text for marker in _DISK_FULL_TEXT):
        raise absent from OSError(errno.ENOSPC, "disk full, as reported on stderr")
    raise absent
