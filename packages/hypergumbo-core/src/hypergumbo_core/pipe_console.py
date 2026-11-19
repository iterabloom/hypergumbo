# SPDX-License-Identifier: AGPL-3.0-or-later
"""A rich ``Console`` whose broken pipe is a ``BrokenPipeError``, not an exit.

WHY. The CLI exits 141 when the reader of its output goes away
(``hypergumbo ... | head``): ``main()`` catches ``BrokenPipeError`` around
command dispatch, points stdout at /dev/null so the shutdown flush stays
quiet, and returns ``EXIT_BROKEN_PIPE`` (owner ruling 2026-11-19, WI-nidun:
one code for the whole CLI, so 1 always means an answer such as
verify-claims' ``violated``). rich does not let that error through. When a
write fails with EPIPE, ``Console._check_buffer`` calls
``Console.on_broken_pipe``, whose default points stdout at /dev/null and
raises ``SystemExit(1)``. ``SystemExit`` is not an ``Exception``, so
``main()`` never saw it and ``hypergumbo symbols <repo> | head -1`` exited 1
with nothing on stderr (WI-vavag).

HOW. :class:`PipeConsole` overrides that hook: it stops further output
(``quiet``) and raises ``BrokenPipeError``, which leaves ``_check_buffer``
like the error a plain ``print`` would have raised, and ``main()`` handles it.
It does not touch stdout itself: ``main()`` does that once for every command,
and a console writing to stderr or to a file must not redirect stdout.

WHERE. Every rich Console hypergumbo-core constructs is a ``PipeConsole``:
``cmd_symbols``' table on stdout and ``sketch``'s representativeness table on
stderr (``2>&1 | head`` makes stderr the same pipe).
``test_rich_broken_pipe.py`` fails on a plain ``Console(...)`` call in the
package's source.
"""
from __future__ import annotations

import errno
import os

from rich.console import Console


class PipeConsole(Console):
    """``rich.console.Console`` that raises ``BrokenPipeError`` on EPIPE."""

    def on_broken_pipe(self) -> None:
        """Stop writing and hand the broken pipe to the caller (see module docstring)."""
        self.quiet = True
        raise BrokenPipeError(errno.EPIPE, os.strerror(errno.EPIPE))
