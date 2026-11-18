# SPDX-License-Identifier: AGPL-3.0-or-later
"""Selecting the files a change affects from a graph of an OLDER tree.

WHAT PROBLEM THIS SOLVES. smart-test's reverse walk (:mod:`graph_rows`) is
cheap; the graph it walks costs a whole-repository analysis. A graph stored
earlier answers for today's tree, but only if three things about its age are
handled, and this module is where they are decided. It holds no I/O: the
caller (``coverage-select graph-select``) asks git and hands the answers in.

1. WHAT TO SEED. Not the change's own files alone. A path from a test to a
   changed file that exists today and not in the stored graph uses an edge
   made since the graph, and an edge is made in a file that changed since the
   graph (over seven pull requests, none of 3,948 differing edges had both
   ends in unchanged files). So the seeds are every file that DIFFERS between
   the graph's tree and today's, plus the change's own files. That is the
   seeding the 741 commit pairs behind this design were measured with. It
   names a superset of what the change alone needs, and the surplus grows
   with the graph's age.

2. WHAT THE SURPLUS COSTS, AND WHEN TO REFUSE. The test files only the
   OTHER seeds reach are tests of other people's merged work: they pass
   through the pull request's CI too. :func:`select` reports them
   (``extra_test_files``) and :func:`verdict` refuses the stored graph when
   there are more than the caller's limit. A fresh analysis is then the
   cheaper answer, and the graph it leaves is of this tree.

3. FILES ADDED SINCE THE GRAPH. A file that did not exist has no node in the
   graph, so seeding it reaches nothing. :func:`judge_added` applies the
   owner's rule (decision 14b of the plan this came from):

   * an added TEST file runs itself: it is put in the answer;
   * any other added file is left to the changed files that MENTION it. A
     file is used through an import, a path or a name, and each of those is a
     line added somewhere else in the change; that file is a seed already;
   * an added file nothing mentions needs a fresh analysis. Over 149 pull
     requests that happened in 3, each a tree of fixtures a test reads by
     scanning a directory;
   * an added ``conftest.py`` always needs one. pytest applies it to every
     test under its directory without anything naming it. None was added in
     the 149 pull requests, so this is a rule without a measurement;
   * the tracker's data (under ``.agent/tracker/`` and
     ``.agent/tracker-workspace/``) needs none. Every ``tracker add`` writes
     a new op file and nothing names it, but no analysis gives tracker data
     a node (the stored graph of 2026-11-18 held none of its paths), so a
     fresh analysis answers nothing more for it. It once cost a run 331 s.

   "Mentions" is textual, as it was measured: the added file's stem as a
   whole word (its full name when the stem is three characters or fewer), in
   a line added to another changed file. A line that holds the file's path
   holds its stem.

THE ANSWER IS A VERDICT (:class:`Verdict`). Only ``USE`` comes with files to
act on. The other two say why a fresh analysis is needed, as does the caller's
own "no stored graph is usable"; none of them may be read as "nothing is
affected".
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Optional, Sequence

from . import graph_rows

#: A test file, as smart-test's change detection recognises one.
_TEST_FILE = re.compile(r"(^|/)tests/(test_|BRANCHES_test_)[^/]*\.py$")

#: Where the tracker keeps its data: no analysis gives a file here a node.
_TRACKER_DATA = (".agent/tracker/", ".agent/tracker-workspace/")

#: A stem this short is matched as the whole file name: ``io`` as a word is
#: everywhere, ``io.py`` is not.
_SHORT_STEM = 3


def is_test_file(path: str) -> bool:
    """Whether ``path`` is a test file (``tests/test_*.py`` at any depth)."""
    return bool(_TEST_FILE.search(path))


def mentions(added: str, text: str) -> bool:
    """Whether ``text`` names the file ``added``: by its stem as a whole word,
    or by its full name when the stem is very short. A text that holds the
    file's path holds both, so a path needs no rule of its own."""
    name = added.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    if len(stem) > _SHORT_STEM:
        return bool(re.search(r"\b" + re.escape(stem) + r"\b", text))
    return name in text


@dataclass(frozen=True)
class AddedFiles:
    """What becomes of each file added since the graph was made."""

    #: Added test files: they are selected themselves.
    run_themselves: tuple[str, ...]
    #: ``(added file, a changed file whose added lines mention it)``.
    mentioned: tuple[tuple[str, str], ...]
    #: ``(added file, why the stored graph cannot answer for it)``.
    need_analysis: tuple[tuple[str, str], ...]
    #: Added tracker data: no analysis would give it a node.
    tracker_data: tuple[str, ...] = ()


def judge_added(added: Iterable[str], added_lines: Mapping[str, str]) -> AddedFiles:
    """Apply the added-file rule (module docstring, point 3).

    ``added`` is every file that exists now and did not in the graph's tree.
    ``added_lines`` maps each changed file to the text of the lines added to
    it since that tree (all of it, for an added file).
    """
    run: list[str] = []
    mentioned: list[tuple[str, str]] = []
    need: list[tuple[str, str]] = []
    tracker: list[str] = []
    for path in sorted(set(added)):
        if path.startswith(_TRACKER_DATA):
            tracker.append(path)
        elif is_test_file(path):
            run.append(path)
        elif path.rsplit("/", 1)[-1] == "conftest.py":
            need.append((path, "an added conftest.py applies to every test under its "
                               "directory and nothing names it"))
        else:
            by = next((other for other in sorted(added_lines)
                       if other != path and mentions(path, added_lines[other])), None)
            if by is None:
                need.append((path, "no other changed file mentions it"))
            else:
                mentioned.append((path, by))
    return AddedFiles(tuple(run), tuple(mentioned), tuple(need), tuple(tracker))


@dataclass(frozen=True)
class Selection:
    """The walk's answer for a later tree, and what it cost in surplus."""

    #: The files named: the walk from every seed, and the added test files.
    files: tuple[str, ...]
    #: Every file the walk was seeded with.
    seeds: tuple[str, ...]
    #: The seeds that are not the change's own: they differ from the graph's
    #: tree for another reason (someone else's merged work, an earlier edit).
    extra_seeds: tuple[str, ...]
    #: Test files only the extra seeds reach.
    extra_test_files: tuple[str, ...]
    added: AddedFiles


def select(
    conn: sqlite3.Connection,
    graph_id: int,
    *,
    changed: Sequence[str],
    differing: Sequence[str],
    added: AddedFiles,
) -> Selection:
    """Walk stored graph ``graph_id`` for a tree that is not the graph's own.

    ``changed`` is the change's own files and ``differing`` every file that
    differs between the graph's tree and the current one (the caller's
    ``git diff`` against the graph's tree, the files not tracked, and the
    files that were uncommitted when the graph was made).
    """
    loaded = graph_rows.load_graph(conn, graph_id)
    own = sorted(set(changed))
    seeds = sorted(set(own) | set(differing))
    from_all = graph_rows.dependent_files(seeds, loaded.nodes, loaded.reverse)
    from_own = set(graph_rows.dependent_files(own, loaded.nodes, loaded.reverse))
    files = sorted(set(from_all) | set(added.run_themselves))
    known = set(own)
    return Selection(
        files=tuple(files),
        seeds=tuple(seeds),
        extra_seeds=tuple(seed for seed in seeds if seed not in known),
        # A test file the change itself edits is a seed of its own, so it is
        # in ``from_own`` whenever the graph has a node for it.
        extra_test_files=tuple(path for path in from_all
                               if is_test_file(path) and path not in from_own),
        added=added,
    )


class Verdict(Enum):
    """Whether a stored graph may answer. Only ``USE`` carries an answer."""

    USE = "use"
    ADDED_FILE = "an added file needs a fresh analysis"
    TOO_FAR_BEHIND = "the stored graph is too far behind this tree"


def verdict(selection: Selection, *, max_extra_tests: int) -> tuple[Verdict, Optional[str]]:
    """Judge a selection: ``(USE, None)``, or the refusal and its reason in
    words a person can act on."""
    if selection.added.need_analysis:
        path, why = selection.added.need_analysis[0]
        more = len(selection.added.need_analysis) - 1
        return Verdict.ADDED_FILE, (
            f"{path} was added since the stored graph and {why}"
            + (f" (and {more} more such file(s))" if more else ""))
    extra = len(selection.extra_test_files)
    if extra > max_extra_tests:
        return Verdict.TOO_FAR_BEHIND, (
            f"{len(selection.extra_seeds)} file(s) differ from the stored graph's tree besides "
            f"this change's own, and they reach {extra} test file(s) this change does not "
            f"(limit {max_extra_tests})")
    return Verdict.USE, None
