#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Detect whether a "watched" long-running command is alive (WI-zajob).

The stop hook pauses when a watched process (``pytest``, ``smart-test``,
``bash ./scripts/auto-pr``, ``bash ./scripts/merge-pr``) is alive, so that
five fast stop events during a legitimate multi-minute command do not
falsely trip the no-progress circuit breaker.

The previous detector used ``pgrep -af <regex>`` and treated any process
whose cmdline CONTAINED the pattern text as a live watched process. This
was structurally wrong: argv-path strings are not argv[0], and any process
invoked with a ``/tmp/pytest-<id>/...`` pathname (pytest's tempdir
fixtures spawn several ``inotifywait`` helpers of this shape) looked
identical to an actual running pytest. When such helpers leaked — observed
etime 1h+ after the parent pytest had long since exited — the hook slept
up to ``watched_max_wait_seconds`` (1800s) doing nothing, blocking every
user interaction until the session was manually interrupted.

This module replaces the substring match with a leading-token match:

- Split the cmdline on whitespace.
- Strip the directory prefix from ``argv[0]`` so ``/usr/bin/pytest`` and
  ``pytest`` compare equal.
- Collapse python version suffixes (``python3.11`` → ``python``) so
  ``python -m pytest`` matches regardless of the interpreter binary name.
- Require each pattern to match the LEADING tokens of the cmdline,
  token-for-token. A pattern like ``bash ./scripts/auto-pr`` matches only
  when tokens 0 and 1 are ``bash`` and ``./scripts/auto-pr`` — a ``vim
  ./scripts/auto-pr`` or ``grep pytest foo.log`` no longer counts.
- Reject ``bash -c`` / ``sh -c`` wrappers explicitly, since ``-c`` means
  "execute this string", and the string's contents are data, not a
  command invocation.

The module has two entry points:

- ``is_watched_cmdline(cmdline, patterns)`` — pure predicate on a single
  cmdline string. Unit-tested directly.
- ``any_watched_alive_in(pgrep_output, patterns)`` — applies the
  predicate to ``pgrep -af`` output (PID-prefixed lines).

The CLI reads ``pgrep -af`` output on stdin and exits 0 if any line is a
real watched process, 1 otherwise. Callers (``stop_logic.sh``) pipe
``pgrep`` straight into ``python3 watched_process.py <pattern>...``.
"""
from __future__ import annotations

import sys
from typing import Iterable, List


def _normalize_argv0(token: str) -> str:
    """Strip the directory prefix and collapse python version suffixes."""
    basename = token.rsplit("/", 1)[-1]
    # Collapse python3.11 / python3 / python2.7 to "python" for matching.
    if basename.startswith("python"):
        tail = basename[len("python"):]
        if tail == "" or all(ch.isdigit() or ch == "." for ch in tail):
            return "python"
    return basename


def _tokens(cmdline: str) -> List[str]:
    return cmdline.split()


def is_watched_cmdline(cmdline: str, patterns: Iterable[str]) -> bool:
    """Return True when *cmdline* represents a live watched process.

    See module docstring for the exact matching rules.
    """
    tokens = _tokens(cmdline)
    if not tokens:
        return False
    # Reject bash -c / sh -c wrappers — -c means "execute string", not
    # "invoke named binary".
    argv0 = _normalize_argv0(tokens[0])
    if argv0 in ("bash", "sh") and len(tokens) >= 2 and tokens[1] == "-c":
        return False
    normalized = [argv0] + tokens[1:]
    for pattern in patterns:
        ptokens = pattern.split()
        if not ptokens:
            continue
        if len(normalized) < len(ptokens):
            continue
        if all(normalized[i] == ptokens[i] for i in range(len(ptokens))):
            return True
    return False


def any_watched_alive_in(pgrep_output: str, patterns: Iterable[str]) -> bool:
    """Apply :func:`is_watched_cmdline` to ``pgrep -af`` output.

    Each non-empty line is expected to be ``PID CMDLINE``. Lines without a
    leading integer PID are ignored (defensive; shouldn't happen in normal
    operation).
    """
    patterns_list = list(patterns)
    for raw_line in pgrep_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        head, _, rest = line.partition(" ")
        if not head.isdigit():
            continue
        if is_watched_cmdline(rest, patterns_list):
            return True
    return False


def main(argv: List[str]) -> int:
    patterns = argv[1:]
    data = sys.stdin.read()
    return 0 if any_watched_alive_in(data, patterns) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
