#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Surface red cron steps at session start (WI-lapof).

WHY THIS EXISTS. Detection of a cron regression is bounded at ~12h by design:
the per-PR self-claims arm was removed by owner decision on 2026-08-30, and that
decision is not reopened here. Reading is not bounded at all. The cron full-suite
self-claims gate went red at ea22d7023c on 2026-09-19 and stayed red for four
days of sessions that merged past it, until someone noticed it by chance
(INV-fugus). It was the third time that symptom occurred. On 2026-09-23 it
happened again: full-suite went red at d31439e6, and it was found only because a
premise check happened to run ``ci-debug cron-status``. LIVE.md's "run
cron-status at session start" was a memory aid, not a mechanism. This is the
mechanism.

WHAT IT SAYS. One line, only when something is red AND no open tracker row
already names it:
- the workflow;
- each failing step;
- the commit of the most recent red verdict;
- where that step FIRST went red, which is where a bisect starts.
The first-red commit is found by walking back one cron verdict at a time
(``ci-debug cron-status <sha>^``) while the same step stays red. The walk is
bounded by a step count and a wall-clock deadline. A lookup that fails is
reported as unknown, never as "first red here".

WHEN IT IS SILENT, and every case is deliberate:
- everything green, or still pending;
- the CI host unreachable, or output it cannot parse, because an outage is not a
  regression, and a nudge that cries wolf gets tuned out;
- every failing step already named by an OPEN row, matched as the step name AND
  one of the red commits (short sha) somewhere in its title or description. The
  step alone would match any row that ever mentioned ``test-all-packages``.
- ``CI`` or ``HG_SKIP_CRON_NUDGE`` set, so test runs and CI make no network call.
Any exception is swallowed and prints nothing. Same contract as
``autopr_convergence_nudge.py``: this can never block a session from starting.

COST, measured on 2026-09-24 against the live CI: 17.4 s cold with one red
workflow. That is one ``cron-status`` call, about 3 s, plus the first-red walk,
whose ``cron-status <sha>^`` walks commits until it finds each cron context,
plus ~1 s for the tracker list. It is 1.2 s from the cache. Both calls go
through approved scripts (AGENTS.md allowed use-case 2). The
NETWORK half is cached in ``.git/cron-status-nudge.json`` for an hour. The
tracker half, suppression, is recomputed on every call, so a row filed in the
meantime silences the nudge at once.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

CACHE_NAME = "cron-status-nudge.json"
TTL_SECONDS = 3600
CALL_TIMEOUT = 15
BUDGET_SECONDS = 25
MAX_LOOKBACK = 4
_RESOLVED = frozenset({"done", "satisfied", "wont_do", "deleted"})

#: ``ci-debug`` arguments -> stdout, or None when the call failed or timed out.
Runner = Callable[[list[str]], Optional[str]]

_WORKFLOW = re.compile(r"^  (\S+)\s+ci/woodpecker/cron/([^:\s]+):")
_VERDICT = re.compile(
    r"^      (FAIL|OK)\s+(?:leg \d+: )?\S+\s+([0-9a-f]{7,40})\s+\S+\s+\((\d+) commit")
_STEP = re.compile(r"^           FAIL\s+(\S+)")


@dataclass(frozen=True)
class RedVerdict:
    """The most recent red cron verdict of one workflow (or matrix leg)."""

    workflow: str
    sha: str
    commits_back: int
    steps: tuple[str, ...]


def parse_cron_status(text: str) -> list[RedVerdict]:
    """Every red verdict in ``ci-debug cron-status`` output, in order."""
    out: list[RedVerdict] = []
    workflow: Optional[str] = None
    current: Optional[list] = None  # [sha, commits_back, steps]

    def flush() -> None:
        if workflow and current is not None and current[2]:
            out.append(RedVerdict(workflow, current[0], current[1], tuple(current[2])))

    for line in text.splitlines():
        wf = _WORKFLOW.match(line)
        if wf:
            flush()
            workflow, current = wf.group(2), None
            continue
        verdict = _VERDICT.match(line)
        if verdict:
            flush()
            current = [verdict.group(2)[:8], int(verdict.group(3)), []] \
                if verdict.group(1) == "FAIL" else None
            continue
        step = _STEP.match(line)
        if step and current is not None:
            current[2].append(step.group(1))
    flush()
    return out


def find_first_red(red: RedVerdict, runner: Runner, *, deadline: float) -> Optional[str]:
    """The earliest commit in the unbroken run of red verdicts sharing a failing
    step with ``red``, or None when a step back could not be read."""
    first = red.sha
    steps = set(red.steps)
    for _ in range(MAX_LOOKBACK):
        if time.monotonic() > deadline:
            return None
        text = runner(["cron-status", f"{first}^", "40"])
        if text is None:
            return None
        if "cron/" + red.workflow not in text:
            return None
        before = [r for r in parse_cron_status(text) if r.workflow == red.workflow]
        if not before or not steps & set(before[0].steps):
            return first
        first = before[0].sha
    return None


def named_by_open_row(step: str, shas: set[str], items: Iterable[dict]) -> bool:
    """Does an OPEN row name this step at one of these red commits?"""
    for item in items:
        if item.get("status") in _RESOLVED:
            continue
        text = f"{item.get('title') or ''}\n{item.get('description') or ''}"
        if step in text and any(sha in text for sha in shas):
            return True
    return False


def _fetch(runner: Runner) -> Optional[list[dict]]:
    """The network half: red verdicts with their first-red commits."""
    text = runner(["cron-status"])
    if text is None or "Most recent cron verdict" not in text:
        return None
    deadline = time.monotonic() + BUDGET_SECONDS
    return [
        {"workflow": r.workflow, "sha": r.sha, "commits_back": r.commits_back,
         "steps": list(r.steps), "first_red": find_first_red(r, runner, deadline=deadline)}
        for r in parse_cron_status(text)
    ]


def _cached(runner: Runner, cache_path: Path, now: float) -> Optional[list[dict]]:
    try:
        data = json.loads(cache_path.read_text())
        if now - float(data["fetched_at"]) < TTL_SECONDS:
            return list(data["red"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    red = _fetch(runner)
    if red is not None:
        try:
            cache_path.write_text(json.dumps({"fetched_at": now, "red": red}))
        except OSError:
            pass
    return red


def compute_line(runner: Runner, *, items: list[dict], cache_path: Path, now: float) -> str:
    """The session-start line, or '' when there is nothing unfiled to say."""
    red = _cached(runner, cache_path, now)
    if not red:
        return ""
    parts: list[str] = []
    for verdict in red:
        shas = {verdict["sha"]} | ({verdict["first_red"]} if verdict["first_red"] else set())
        steps = [s for s in verdict["steps"] if not named_by_open_row(s, shas, items)]
        if not steps:
            continue
        first = verdict["first_red"]
        since = (f"first red at {first}" if first
                 else "first-red commit not determined")
        parts.append(
            f"{verdict['workflow']} ({', '.join(f'`{s}`' for s in steps)}) red at "
            f"{verdict['sha']}, {verdict['commits_back']} commit(s) back, {since}")
    if not parts:
        return ""
    return ("The cron CI is RED and no open tracker row names it: "
            + "; ".join(parts)
            + ". Read `./scripts/ci-debug cron-status`, then fix it or file a row "
              "naming the step and commit.")


def _ci_debug_runner(repo_root: Path) -> Runner:
    def run(args: list[str]) -> Optional[str]:
        try:
            proc = subprocess.run(  # noqa: S603 -- the repo's own approved script, fixed argv
                [str(repo_root / "scripts" / "ci-debug"), *args], cwd=repo_root,
                capture_output=True, text=True, timeout=CALL_TIMEOUT, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout or None
    return run


def _open_items(repo_root: Path) -> list[dict]:
    proc = subprocess.run(  # noqa: S603 -- the repo's own tracker CLI, fixed argv
        [str(repo_root / "scripts" / "tracker"), "--json", "list"], cwd=repo_root,
        capture_output=True, text=True, timeout=CALL_TIMEOUT, check=False)
    data = json.loads(proc.stdout)
    return [i for i in data if isinstance(i, dict) and i.get("status") not in _RESOLVED]


def main(argv: list[str]) -> int:
    if os.environ.get("CI") or os.environ.get("HG_SKIP_CRON_NUDGE"):
        return 0
    try:
        repo_root = Path(argv[0]).resolve()
        line = compute_line(
            _ci_debug_runner(repo_root), items=_open_items(repo_root),
            cache_path=repo_root / ".git" / CACHE_NAME, now=time.time())
    except Exception:  # a session must never fail to start over this
        return 0
    if line:
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
