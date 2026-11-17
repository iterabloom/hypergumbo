#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Surface the scheduled runs' verdict at session start (WI-lapof, WI-zutub).

WHY THIS EXISTS. A red scheduled run (the full suite twice a day, the nightly
matrix) can stay red across days of sessions that merge past it, found only
when someone happens to look (history: INV-fugus; the full suite red in
pipelines 6453 and 6480 through the 10.0.0 release). A "check CI at session
start" note is a memory aid, not a mechanism. This is the mechanism.

WHERE THE VERDICT COMES FROM. Each scheduled run's last step,
``scripts/publish-scheduled-verdict``, pushes ``scheduled/<workflow>[-<leg>].json``
to the ``badges`` branch (its docstring holds the schema). This module reads
those files from ``origin/badges`` with local git commands: ``git ls-tree`` and
``git show``. No virtualenv, no ``ci-debug``, no forge API call. The previous
version called ``ci-debug cron-status`` and printed NOTHING when it failed,
which it always did in a hook's environment (no virtualenv on PATH) and
whenever its cold call (41 s) outran the hook's 5 s timeout: the nudge that
exists to stop a red run going unread went unread itself.

FRESHNESS. Before reading, one ``git fetch -q origin badges``, bounded by
``FETCH_TIMEOUT`` (its process group is killed on timeout, so an ssh child
cannot hold the hook open). If it fails or times out the ref is read as it is
and the line says "origin/badges not refreshed". A verdict too old for its
workflow (``ALLOWANCE_HOURS``) is itself reported: a run killed with its
runner, or a workflow that stopped compiling, publishes nothing, and age is
the only sign of that.

WHAT IT SAYS. One line, its parts joined with ``; ``:

- a verdict whose status is ``failed``: workflow, leg, each ``not_passed``
  step, the commit, the verdict's age, and the first-red commit -- found by
  walking the file's history on ``origin/badges`` (at most ``MAX_HISTORY``
  versions) back while a verdict shares a ``not_passed`` step with the newest
  one. A walk that cannot read a version, or that never reaches a verdict
  without those steps, says "first-red commit not determined": never a guess;
- a verdict older than its allowance: "newest verdict is N h old: the
  scheduled run has not published since";
- no ``origin/badges``, no verdict for a workflow in ``ALLOWANCE_HOURS``, or a
  verdict that does not parse: "Scheduled-run status not checked: <reason>.";
- anything unexpected: the same "not checked" line naming the exception's
  class. It never raises and always exits 0: a session must start.

WHEN IT IS SILENT. Every verdict passed and is within its allowance. Or:

- every red step is named by an OPEN tracker row, matched as the step name AND
  one of the red commits (newest or first red, 8 hex) in its title or
  description; the step alone would match any row that ever mentioned
  ``test-all-packages``. The tracker is consulted only when something is red,
  bounded by ``TRACKER_TIMEOUT``, through ``scripts/tracker`` with the
  repository's ``.venv/bin`` first on PATH (a hook gets no activated
  virtualenv). When it cannot be consulted the red part is printed with
  "(tracker not consulted)" rather than dropped;
- ``CI`` or ``HG_SKIP_CRON_NUDGE`` set: no fetch, no line, so test runs and CI
  make no network call.

COST. A green, fresh verdict: the bounded fetch plus a few local git calls.
A red one adds the history walk (local) and the bounded tracker call.
"""
from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REF = "origin/badges"
DIR = "scheduled/"
FETCH_TIMEOUT = 2.0
TRACKER_TIMEOUT = 2.0
GIT_TIMEOUT = 2.0
MAX_HISTORY = 20

#: Hours after which a workflow's newest verdict counts as missing. The full
#: suite fires every 12 h (26 = two firings plus slack); the nightly every 24 h.
ALLOWANCE_HOURS = {"full-suite": 26, "nightly": 50}

_RESOLVED = frozenset({"done", "satisfied", "wont_do", "deleted"})
_REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_PREFIX")
_ADVICE = "Run `./scripts/ci-debug cron-status`."


class NotChecked(Exception):
    """The status cannot be determined; the message is the reason."""


def not_checked(reason: str) -> str:
    return f"Scheduled-run status not checked: {reason}. {_ADVICE}"


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _REPO_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.update(extra)
    return env


def _bounded(argv: list[str], *, cwd: Path, timeout: float,
             env: Optional[dict[str, str]] = None) -> Optional[str]:
    """stdout of ``argv``, or None when it failed or outran ``timeout``.
    Runs in its own process group, killed whole on timeout."""
    try:
        proc = subprocess.Popen(  # noqa: S603 -- fixed argv: git, or the repo's own tracker
            argv, cwd=cwd, env=env or _env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:  # pragma: no cover -- the group exited between the timeout and the kill
            pass
        proc.communicate()
        return None
    return out.decode("utf-8", "replace") if proc.returncode == 0 else None


def _git(root: Path, *args: str) -> Optional[str]:
    return _bounded(["git", "-C", str(root), *args], cwd=root, timeout=GIT_TIMEOUT)


def refresh(root: Path) -> bool:
    """One bounded ``git fetch -q origin badges``; True when it succeeded."""
    return _bounded(["git", "-C", str(root), "fetch", "-q", "origin", "badges"],
                    cwd=root, timeout=FETCH_TIMEOUT) is not None


def parse_verdict(text: str) -> dict[str, Any]:
    """A schema-1 verdict, or ValueError."""
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("not a schema-1 verdict")
    if not isinstance(data.get("workflow"), str) or data.get("status") not in ("passed", "failed"):
        raise ValueError("no workflow or status")
    if not isinstance(data.get("commit"), str) or not isinstance(data.get("steps"), list):
        raise ValueError("no commit or steps")
    for step in data["steps"]:
        if not isinstance(step, dict) or not isinstance(step.get("name"), str):
            raise ValueError("a step without a name")
    finished = datetime.strptime(str(data.get("finished_at")), "%Y-%m-%dT%H:%M:%SZ")
    data["_finished"] = finished.replace(tzinfo=timezone.utc).timestamp()
    return data


def not_passed(verdict: dict[str, Any]) -> list[str]:
    return [s["name"] for s in verdict["steps"] if s.get("result") != "passed"]


def read_verdicts(root: Path) -> tuple[list[str], dict[str, dict[str, Any]], list[str]]:
    """(verdict paths, path -> parsed verdict, problems). Raises NotChecked
    when there is no ref or no verdict file at all."""
    if _git(root, "rev-parse", "--verify", "-q", f"{REF}^{{commit}}") is None:
        raise NotChecked(f"no {REF} ref (run `git fetch origin badges`)")
    listing = _git(root, "ls-tree", "--name-only", REF, DIR)
    if listing is None:
        raise NotChecked(f"cannot list {DIR} on {REF}")
    paths = [p for p in listing.split()
             if p.endswith(".json") and not p.endswith("-badge.json")]
    if not paths:
        raise NotChecked(f"no verdict file under {DIR} on {REF}")
    verdicts: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for path in sorted(paths):
        text = _git(root, "show", f"{REF}:{path}")
        try:
            if text is None:
                raise ValueError("unreadable")
            verdicts[path] = parse_verdict(text)
        except ValueError as exc:
            problems.append(f"{REF}:{path} does not parse ({exc})")
    return paths, verdicts, problems


def first_red(root: Path, path: str, newest: dict[str, Any]) -> tuple[Optional[str], str]:
    """(first-red commit or None, why None). Walks the file's history back
    while each older verdict shares a not_passed step with the newest."""
    log = _git(root, "log", f"--max-count={MAX_HISTORY}", "--format=%H", REF, "--", path)
    if log is None:
        return None, "history unreadable"
    red = set(not_passed(newest))
    first = newest["commit"]
    versions = log.split()
    for sha in versions[1:]:
        text = _git(root, "show", f"{sha}:{path}")
        try:
            if text is None:
                raise ValueError("unreadable")
            older = parse_verdict(text)
        except ValueError:
            return None, f"version {sha[:8]} unreadable"
        if not red & set(not_passed(older)):
            return first, ""
        first = older["commit"]
    return None, f"red in all {len(versions)} verdicts read, oldest at {first[:8]}"


def open_items(root: Path) -> Optional[list[dict[str, Any]]]:
    """Open tracker items, or None when the tracker could not be consulted."""
    venv_bin = root / ".venv" / "bin"
    env = _env(PATH=f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}")
    out = _bounded([str(root / "scripts" / "tracker"), "--json", "list"], cwd=root,
                   timeout=TRACKER_TIMEOUT, env=env)
    if out is None:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    return [i for i in data if isinstance(i, dict) and i.get("status") not in _RESOLVED]


def named_by_open_row(step: str, shas: set[str], items: list[dict[str, Any]]) -> bool:
    """Does one of these rows (``open_items`` keeps only the open ones) name
    this step at one of these red commits?"""
    for item in items:
        text = f"{item.get('title') or ''}\n{item.get('description') or ''}"
        if step in text and any(sha in text for sha in shas):
            return True
    return False


def _label(verdict: dict[str, Any]) -> str:
    leg = verdict.get("leg")
    return f"{verdict['workflow']} {leg}" if leg else verdict["workflow"]


def compute_line(root: Path, *, now: float, refreshed: bool) -> str:
    """The session-start line, or '' when there is nothing to say."""
    try:
        all_paths, verdicts, problems = read_verdicts(root)
    except NotChecked as exc:
        reason = str(exc) + ("" if refreshed else f"; {REF} not refreshed")
        return not_checked(reason)
    stale: list[str] = []
    red: list[tuple[str, dict[str, Any], Optional[str], str]] = []
    for path, verdict in verdicts.items():
        if verdict["status"] == "failed":
            red.append((path, verdict, *first_red(root, path, verdict)))
        allowance = ALLOWANCE_HOURS.get(verdict["workflow"])
        hours = (now - verdict["_finished"]) / 3600
        if allowance is not None and hours > allowance:
            stale.append(f"{_label(verdict)}: newest verdict is {math.floor(hours)} h old: "
                         "the scheduled run has not published since")
    items = open_items(root) if red else []
    parts: list[str] = []
    for _path, verdict, first, why in red:
        commit = verdict["commit"][:8]
        shas = {commit} | ({first[:8]} if first else set())
        steps = [s for s in not_passed(verdict)
                 if items is None or not named_by_open_row(s, shas, items)]
        if not steps:
            continue
        since = f"first red at {first[:8]}" if first else f"first-red commit not determined ({why})"
        age = math.floor((now - verdict["_finished"]) / 3600)
        consulted = " (tracker not consulted)" if items is None else ""
        parts.append(
            f"scheduled run RED: {_label(verdict)} ({', '.join(f'`{s}`' for s in steps)}) "
            f"not passed at {commit}, verdict {age} h old, {since}{consulted}")
    if parts:
        parts.append("fix it or file a tracker row naming the step and commit")
    parts.extend(stale)
    stems = [Path(p).stem for p in all_paths]
    missing = [w for w in ALLOWANCE_HOURS
               if not any(st == w or st.startswith(f"{w}-") for st in stems)]
    if missing:
        problems.append(f"no verdict for {', '.join(missing)} on {REF}")
    parts.extend(not_checked(p) for p in problems)
    if not parts:
        return ""
    if not refreshed:
        parts.append(f"{REF} not refreshed")
    return "; ".join(parts)


def main(argv: list[str]) -> int:
    if os.environ.get("CI") or os.environ.get("HG_SKIP_CRON_NUDGE"):
        return 0
    try:
        root = Path(argv[0]).resolve()
        line = compute_line(root, now=time.time(), refreshed=refresh(root))
    except Exception as exc:  # a session must never fail to start over this
        line = not_checked(f"unexpected {type(exc).__name__}")
    if line:
        try:
            print(line)
        except OSError:  # pragma: no cover -- stdout closed by the caller
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
