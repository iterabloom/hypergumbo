# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a program a test started could have done that the test's own process
did not show.

The recorder plugin (``scripts/pytest_plugins/pytest_source_reads.py``) writes
every process a test starts as ``<audit event>:<program>``: the event alone
when it names no program (``os.fork``), and ``git-elsewhere`` for a ``git``
whose directory, arguments and ``GIT_*`` variables all point outside the
repository. Nothing a child does is seen by the recorder or by coverage, so
two rules ask, of the same record, two DIFFERENT questions, and a program can
answer them differently:

* the prose-only rule (:mod:`hypergumbo_core.prose_only`) asks whether the
  child could have READ A FILE OF THE REPOSITORY as data. ``gitleaks`` can:
  reading files is its job. A ``git`` run in another repository cannot.
* test selection by coverage (:mod:`hypergumbo_core.selection_index`) asks
  whether the child could have RUN PYTHON, and with it package source that
  coverage did not measure. ``gitleaks`` cannot: it is a Go binary that scans
  text. A ``git`` run in another repository can: it runs that repository's
  hooks, and our own tests install hooks that run Python.

Both lists are BY NAME, each entry with its reason, and a program in neither
is assumed able to do both. The name is the basename of what was executed, so
a list entry is a claim about every program of that name on the machine that
ran the tests: keep the lists to programs whose behaviour does not depend on
their arguments. ``bash``, ``env``, ``ssh`` and ``git`` run what they are told
to, and are in neither list for that reason.

This module imports nothing from the package: the plugin's record is read by
two modules that must not depend on each other for it.
"""
from __future__ import annotations

from typing import Mapping

#: The record format this knowledge is about; ``pytest_source_reads.VERSION``.
#: 3: a ``git`` that cannot reach this repository is recorded
#: (``git-elsewhere``) where it used to leave nothing, so a record of version
#: 2 that shows no spawn does not show that the test started none.
RECORDER_VERSION = 3

#: What the recorder writes for a ``git`` that cannot reach this repository
#: (the plugin spells the same word; ``tests/test_pytest_source_reads.py``
#: holds the two together).
GIT_ELSEWHERE = "git-elsewhere"

#: Programs that cannot read a repository ``.py`` file whatever they are
#: given, so a test that starts only these is not opaque to the prose-only
#: rule. ``rust-analyzer`` is here because the pipeline probes for it in most
#: tests, and it reads Rust projects only.
INERT_PROGRAMS = frozenset({
    "rust-analyzer", "rustup", "ldconfig", "sleep", "true", "false", "echo",
    "pwd", "mktemp", GIT_ELSEWHERE,
})

#: Programs that cannot run Python whatever they are given, each with why.
#: NOT HERE, on purpose: ``rustup`` (``rustup run`` executes a command),
#: ``git`` in any repository (hooks), shells, ``env``, ``ssh``.
CANNOT_RUN_PYTHON: Mapping[str, str] = {
    "gitleaks": "a Go binary that scans files as text",
    "rust-analyzer": "a language server binary; it loads Rust projects",
    "ldconfig": "prints the dynamic linker's cache",
    "sleep": "waits", "true": "exits 0", "false": "exits 1",
    "echo": "prints its arguments", "pwd": "prints a directory",
    "mktemp": "creates a file",
}


def _programs(spawns: str) -> list[tuple[str, str]]:
    """``(spawn, program)`` for each recorded spawn of a comma-joined list."""
    return [(spawn, spawn.rpartition(":")[2]) for spawn in spawns.split(",") if spawn]


def may_read_repository(spawns: str) -> list[str]:
    """The recorded spawns that could have read a repository file."""
    return [spawn for spawn, program in _programs(spawns) if program not in INERT_PROGRAMS]


def may_run_python(spawns: str) -> list[str]:
    """The programs (or bare events) among the recorded spawns that could
    have run Python, each once, sorted."""
    return sorted({program for _, program in _programs(spawns)
                   if program not in CANNOT_RUN_PYTHON})
