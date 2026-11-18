# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two questions about a program a test started, answered by name.

"Could it have read a repository file?" (the prose-only rule) and "could it
have run Python?" (selection by coverage) are different properties, and the
programs that tell them apart are the ones pinned here: ``gitleaks`` reads
files and cannot run Python; a ``git`` in another repository cannot read ours
and runs hooks.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.started_programs import (
    CANNOT_RUN_PYTHON,
    GIT_ELSEWHERE,
    INERT_PROGRAMS,
    may_read_repository,
    may_run_python,
)

POPEN = "subprocess.Popen"


def _spawns(*programs: str) -> str:
    return ",".join(f"{POPEN}:{program}" for program in programs)


def test_no_spawn_is_no_program() -> None:
    assert may_run_python("") == []
    assert may_read_repository("") == []


@pytest.mark.parametrize("program", ["python", "python3", "bash", "sh", "env", "git",
                                     "tracker", "ssh", "rustup", GIT_ELSEWHERE])
def test_a_program_that_runs_what_it_is_told_may_run_python(program: str) -> None:
    assert may_run_python(_spawns(program)) == [program]


def test_gitleaks_cannot_run_python_and_can_read_the_repository() -> None:
    assert may_run_python(_spawns("gitleaks")) == []
    assert may_read_repository(_spawns("gitleaks")) == [f"{POPEN}:gitleaks"]


def test_git_in_another_repository_cannot_read_ours_and_can_run_python() -> None:
    assert may_read_repository(_spawns(GIT_ELSEWHERE)) == []
    assert may_run_python(_spawns(GIT_ELSEWHERE)) == [GIT_ELSEWHERE]


def test_git_that_can_reach_the_repository_does_both() -> None:
    assert may_read_repository(_spawns("git")) == [f"{POPEN}:git"]
    assert may_run_python(_spawns("git")) == ["git"]


def test_an_event_that_names_no_program_is_assumed_able_to_do_both() -> None:
    assert may_run_python("os.fork") == ["os.fork"]
    assert may_read_repository("os.fork") == ["os.fork"]


def test_one_able_program_among_exempt_ones_is_named_once() -> None:
    spawns = _spawns("rust-analyzer", "gitleaks", "python") + f",os.system:python,{POPEN}:sleep"
    assert may_run_python(spawns) == ["python"]
    assert may_read_repository(spawns) == [
        f"{POPEN}:gitleaks", f"{POPEN}:python", "os.system:python"]


def test_every_exemption_carries_its_reason() -> None:
    assert all(reason.strip() for reason in CANNOT_RUN_PYTHON.values())


def test_the_two_lists_differ_exactly_where_the_properties_do() -> None:
    """Drop an entry from either list and this names it."""
    assert set(CANNOT_RUN_PYTHON) - INERT_PROGRAMS == {"gitleaks"}
    assert INERT_PROGRAMS - set(CANNOT_RUN_PYTHON) == {"rustup", GIT_ELSEWHERE}
