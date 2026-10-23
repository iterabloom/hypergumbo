# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-lanos: a child's exit status gets ONE verdict, whichever call hands it back.

THE DISAGREEMENT. Two catalogue rows minted an ``untrusted_input`` source for an
exit status -- python's ``os`` wait family and rust's ``Child.wait`` /
``try_wait``, rowed ``ipc_recv`` "Reaping a child collects its exit status" --
while every call that returns the SAME value one step earlier minted nothing:
``subprocess.call``, ``os.system``, ``Command::status``, ``Cmd.Run``,
``rawSystem``. INV-lozat had already ruled the second reading: "an exit status
is not the child's message". One value, two verdicts.

THE RULING (owner, 2026-10-22; ADR-0049 §Ruling 4, "An exit status is not the
child's message"). The rows go, so every status-only call mints nothing. A
finding needs the value to reach a sink's ARGUMENT, and an exit status is
consumed as a branch condition; the opposite direction (mint at every
status-returning call) was measured twice and 0 of 120 sampled added findings
carried a status to a sink argument. Ruling 3's finding-level test was run
before this merged: 11 repositories, 0 situations removed or added, 0 verdicts
moved (lab notebook ``lanos_removal_10222026/``).

THE POPULATION IS CUT BY RETURN VALUE, NOT BY NAME, each member read against its
API (ported from the omission-side branch, whose rows were not merged):
``call`` / ``Popen.wait`` / ``Popen.poll`` return ``returncode``; ``os.system``
the wait status; ``Command::status`` an ``ExitStatus``; ``Cmd.Run`` an
``error`` that is an ``*exec.ExitError`` carrying the status; ``system`` /
``rawSystem`` / ``waitForProcess`` an ``ExitCode``. Three status-only calls
have no row on dev at all and are not asserted on, since "mints nothing" would
pass vacuously for them: go ``Cmd.Wait`` and ``os.Process.Wait``, haskell
``getProcessExitCode``. ``subprocess.run`` is not a member: its
``CompletedProcess`` also carries captured stdout (WI-lojar).

THE RESIDUAL, PINNED (INV-sozak). A status read out of an OUTPUT-carrying result
still mints, because the row taints the whole returned value:
``getstatusoutput()[0]``, ``wait_with_output().status``. That is one value with a
second verdict, accepted for now (ADR-0052's field-sensitivity cost). The pins
below assert TODAY'S state, so they go red when INV-sozak lands and are
re-pointed then.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from hypergumbo_core.cli import main
from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.taint import _derive_auto_imports_from_io_primitives
from hypergumbo_core.verify_claims import (
    CHOICE_SHAPED_SOURCE_NAMES,
    is_choice_shaped_source,
)

_CATALOG_DIR = (
    Path(__file__).resolve().parents[1]
    / "src" / "hypergumbo_core" / "io_primitives"
)

Key = tuple[str, str, str, str]

#: The rows this change REMOVED: no catalogue row of any boundary names them now.
REMOVED: tuple[Key, ...] = (
    ("python", "os", "wait", "function"),
    ("python", "os", "waitpid", "function"),
    ("python", "os", "wait3", "function"),
    ("python", "os", "wait4", "function"),
    ("python", "os", "waitid", "function"),
    ("rust", "std::process::Child", "wait", "method"),
    ("rust", "std::process::Child", "try_wait", "method"),
)

#: Status-only calls that keep their LAUNCH row (a subprocess sink) and mint no
#: source -- the reading REMOVED is brought into line with.
STATUS_ONLY_LAUNCHES: tuple[Key, ...] = (
    ("python", "subprocess", "call", "function"),
    ("python", "os", "system", "function"),
    ("python", "subprocess.Popen", "wait", "method"),
    ("python", "subprocess.Popen", "poll", "method"),
    ("rust", "std::process::Command", "status", "method"),
    ("go", "os/exec.Cmd", "Run", "method"),
    ("haskell", "System.Process", "system", "function"),
    ("haskell", "System.Process", "rawSystem", "function"),
    ("haskell", "System.Process", "waitForProcess", "function"),
)

#: The child's OUTPUT stays represented: these return what the child wrote.
OUTPUT_CARRYING: tuple[Key, ...] = (
    ("python", "subprocess", "check_output", "function"),
    ("python", "subprocess", "getoutput", "function"),
    ("python", "subprocess", "getstatusoutput", "function"),
    ("python", "subprocess.Popen", "communicate", "method"),
    ("rust", "std::process::Command", "output", "method"),
    ("rust", "std::process::Child", "wait_with_output", "method"),
    ("haskell", "System.Process", "readProcess", "function"),
    ("haskell", "System.Process", "readProcessWithExitCode", "function"),
)


def _ids(member: Key) -> str:
    return f"{member[0]}:{member[1]}.{member[2]}"


@pytest.fixture(scope="module")
def derived() -> tuple[dict[Key, Any], dict[Key, Any]]:
    sources, sinks, _amb = _derive_auto_imports_from_io_primitives(_CATALOG_DIR)

    def index(table: dict[str, list[Any]]) -> dict[Key, Any]:
        return {
            (lang, e.module, e.name, e.kind): e
            for lang, entries in table.items()
            for e in entries
        }

    return index(sources), index(sinks)


def _rowed(member: Key) -> bool:
    lang, module, name, kind = member
    return any(
        (p.module, p.name, p.kind) == (module, name, kind)
        for p in load_catalog(lang).primitives
    )


class TestOneValueOneVerdictInTheCatalogue:
    @pytest.mark.parametrize("member", REMOVED, ids=_ids)
    def test_the_reaping_rows_are_gone(
        self, derived: Any, member: Key,
    ) -> None:
        assert not _rowed(member), f"{_ids(member)} is rowed again (WI-lanos)"
        assert derived[0].get(member) is None

    @pytest.mark.parametrize("member", STATUS_ONLY_LAUNCHES, ids=_ids)
    def test_a_status_only_launch_is_a_sink_and_mints_no_source(
        self, derived: Any, member: Key,
    ) -> None:
        """Rowed (so the absence below is not vacuous), still the subprocess
        sink it was, and no source: the same answer as the reaping calls."""
        assert _rowed(member), f"{_ids(member)} lost its launch row"
        sink = derived[1].get(member)
        assert sink is not None and sink.zone == "subprocess"
        assert derived[0].get(member) is None

    @pytest.mark.parametrize("member", OUTPUT_CARRYING, ids=_ids)
    def test_the_childs_output_still_mints(
        self, derived: Any, member: Key,
    ) -> None:
        source = derived[0].get(member)
        assert source is not None, f"{_ids(member)} stopped minting the child's output"
        assert (source.taint_label, source.source_boundary) == ("untrusted_input", "ipc_recv")

    def test_the_four_languages_are_covered(self) -> None:
        assert {m[0] for m in REMOVED + STATUS_ONLY_LAUNCHES} == {
            "python", "rust", "go", "haskell",
        }


class TestNoChoiceShapedEntryOutlivesItsSource:
    """WI-jivih's ``choice_shaped_source`` caveat named the exit-status family
    because those rows minted. With no source left under those names, the
    entries would only ever match something else that happens to be called
    ``wait``."""

    @pytest.mark.parametrize(
        "name", ["wait", "waitpid", "wait3", "wait4", "waitid", "try_wait"],
    )
    def test_the_exit_status_names_are_not_choice_shaped(self, name: str) -> None:
        assert name not in CHOICE_SHAPED_SOURCE_NAMES
        assert not is_choice_shaped_source(name)

    def test_the_other_families_still_are(self) -> None:
        assert is_choice_shaped_source("accept")
        assert is_choice_shaped_source("gethostbyname")


# ---------------------------------------------------------------------------
# Behavioural: the production CLI, cold cache, one repository per call shape so
# each verdict is attributable to exactly one source call.
# ---------------------------------------------------------------------------

_CLAIMS = """claims:
  - id: untrusted-input-no-subprocess
    text: Data from an IPC channel is never passed to a subprocess.
    constraint:
      taint_flow:
        source_taint: untrusted_input
        prohibited_sink_zone: subprocess
"""

_PY_WAITPID = """import os
import subprocess


def reap(pid):
    _, status = os.waitpid(pid, 0)
    subprocess.Popen(["echo", str(status)])
"""

_PY_CALL = """import subprocess


def launch():
    rc = subprocess.call(["false"])
    subprocess.Popen(["echo", str(rc)])
"""

_PY_GETSTATUSOUTPUT = """import subprocess


def launch():
    status = subprocess.getstatusoutput("false")[0]
    subprocess.Popen(["echo", str(status)])
"""

# A TYPED binding: a Child method on an unannotated binding mints no source at
# all (WI-rupah), which would make the agreement below hold for the wrong reason.
_RS_CHILD_WAIT = """use std::process::{Child, Command};

fn reap() {
    let mut child: Child = Command::new("false").spawn().unwrap();
    let status = child.wait().unwrap();
    Command::new(status.to_string()).spawn().unwrap();
}
"""

_RS_STATUS = """use std::process::Command;

fn launch() {
    let mut cmd: Command = Command::new("false");
    let status = cmd.status().unwrap();
    Command::new(status.to_string()).spawn().unwrap();
}
"""

_RS_WAIT_WITH_OUTPUT_STATUS = """use std::process::{Child, Command};

fn reap() {
    let child: Child = Command::new("false").spawn().unwrap();
    let out = child.wait_with_output().unwrap();
    Command::new(out.status.to_string()).spawn().unwrap();
}
"""


def _verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
            filename: str, program: str) -> dict[str, Any]:
    repo = tmp_path / "repo"
    (repo / filename).parent.mkdir(parents=True, exist_ok=True)
    (repo / filename).write_text(program)
    claims = tmp_path / "claims.yaml"
    claims.write_text(_CLAIMS)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        main(["verify-claims", str(repo), "--claims", str(claims),
              "--format", "json", "--no-default-overlays"])
    (verdict,) = json.loads(buf.getvalue())["verdicts"]
    return verdict


def _shape(verdict: dict[str, Any]) -> tuple[str, int]:
    return verdict["verdict"], verdict["evidence_count"]


@pytest.mark.parametrize(
    ("filename", "reaping", "launching"),
    [
        ("main.py", _PY_WAITPID, _PY_CALL),
        ("src/main.rs", _RS_CHILD_WAIT, _RS_STATUS),
    ],
    ids=["python-waitpid-vs-call", "rust-child-wait-vs-status"],
)
def test_the_status_reaching_a_launch_reads_one_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    filename: str, reaping: str, launching: str,
) -> None:
    """The item's own repro: os.waitpid read ``violated`` and subprocess.call
    ``confirmed_with_caveats`` for the same flow. Now both give one answer,
    with no evidence row."""
    a = _verify(tmp_path / "a", monkeypatch, filename, reaping)
    b = _verify(tmp_path / "b", monkeypatch, filename, launching)
    assert _shape(a) == _shape(b), (a["verdict"], b["verdict"])
    assert a["evidence_count"] == 0 and a["verdict"] != "violated"


@pytest.mark.parametrize(
    ("filename", "program", "primitive"),
    [
        ("main.py", _PY_GETSTATUSOUTPUT, "subprocess.getstatusoutput"),
        ("src/main.rs", _RS_WAIT_WITH_OUTPUT_STATUS, "std::process::Child.wait_with_output"),
    ],
    ids=["python-getstatusoutput-0", "rust-wait-with-output-status"],
)
def test_the_residual_disagreement_is_todays_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    filename: str, program: str, primitive: str,
) -> None:
    """INV-sozak, PINNED: the same status read out of an output-carrying result
    still reads ``violated``, because the whole value is the source. This test
    is ALSO the live control that the harness above can say ``violated`` at
    all. When field-sensitivity lands it goes red; re-point it then."""
    verdict = _verify(tmp_path, monkeypatch, filename, program)
    assert verdict["verdict"] == "violated", verdict.get("details")
    assert any(
        primitive in e.get("source_primitives", []) for e in verdict["evidence"]
    ), [e.get("source_primitives") for e in verdict["evidence"]]
