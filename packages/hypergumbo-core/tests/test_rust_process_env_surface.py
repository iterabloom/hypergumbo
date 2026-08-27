# SPDX-License-Identifier: AGPL-3.0-or-later
"""The enumerated I/O surface of rust's std::process and std::env (WI-dadog).

WHY THIS ITEM PAYS WHERE WI-gihos DID NOT. WI-gihos measured that the rust
catalogue matches ZERO method-kind primitives across 11,691 call edges: rust
receivers arrive unresolved (``rust:external:...``, null ``dst_ref``), and
:func:`~hypergumbo_core.io_boundary.gate_named_entry` correctly refuses a
method-kind hit with no module hint (INV-linub L3/L4). FUNCTION-kind rows are
the ones that fire — measured, 23 distinct ones do. ``std::env`` is almost
entirely free functions, so ITS rows are live rather than inert. The
``std::process`` method rows below are inert today for the same reason
WI-gihos's were, and that is stated rather than discovered later.

EVERY BOUNDARY HERE IS FIXED BY CROSS-LANGUAGE PRECEDENT, NOT CHOSEN.
python.yaml already rules ``os.putenv`` / ``os.unsetenv`` / ``os.chdir`` as
``env_write``, ``os.kill`` as ``ipc_send`` ("signalling another process"),
``os.wait`` / ``waitpid`` as ``ipc_recv`` ("reaping a child collects its exit
status — data received from another process"), and ``tempfile.gettempdir`` as
``host_info_read``. A catalogue that disagrees with itself across languages is
the defect INV-nular's sweep exists to find, so these follow python rather than
re-litigating each name. rust.yaml's own ``std::os::unix::fs`` note already
promised this for one of them: *"chdir has no row here: python rules it
env_write, and rust's std::env::set_current_dir is not yet audited."* It is
audited now.

THE JUDGMENT THIS ITEM WAS FILED TO MAKE — and it is recorded as a judgment,
with the argument that carries it, because the cross-language answer does NOT
exist. ``std::thread`` reads ``RUST_MIN_STACK`` through ``env::var`` when it
spawns. Does that make ``thread::spawn`` an ``env_read`` boundary for a caller
who never names an environment variable? **No, and std::thread stays absent
entirely.** Four reasons, weakest first:

* rust.yaml's header ALREADY declares ``std::sync`` / ``std::thread``
  intentionally absent as concurrency primitives. This item does not overturn a
  standing decision without a reason to.
* The owner's boundary test — *"is it changing some state outside of itself"* —
  answers no: a thread lives inside the process. WI-pavob applied exactly this
  to rule locks OUT for thread contention.
* **THE DISCRIMINATION ARGUMENT, which is the one that actually decides it.**
  The read is INTERNAL: ``RUST_MIN_STACK`` sizes the new thread's stack and its
  value never reaches the caller. If an internal environment read made a
  boundary, then locale lookups, ``TZ``, ``RUST_BACKTRACE`` and a long tail of
  stdlib machinery would all qualify, and ``env_read`` would stop
  discriminating between "this code reads configuration" and "this code is
  built on a stdlib that does". A boundary that everything satisfies classifies
  nothing.
* python is **SILENT**, not supportive, and that is stated rather than dressed
  up as precedent: ``threading`` appears only in python.yaml's
  ``stdlib_modules`` name list, with no rows and no completeness entry. Absence
  of a decision is not a decision (LIVE.md rule 7).

Reversible: if a repository is ever found whose behaviour genuinely turns on
RUST_MIN_STACK, this is the paragraph to reopen.

A FOURTH PROBE BUG WAS FOUND PRODUCING THIS LIST, and like the first three it
returned a plausible wrong answer rather than an error: the receiver regex
required a leading ``&``, so ``fn wait_with_output(mut self)`` and 46 other
BY-VALUE receivers in ``process.rs`` read as ASSOCIATED FUNCTIONS. Kind is
exactly what decides matchability (INV-nular), so that would have shipped
``wait_with_output`` as a function-kind row that no method call could match.
Re-checked against WI-gihos's 70 already-merged rows: none was affected.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import load_catalog


def _rows(module: str) -> dict[str, tuple[str, str]]:
    catalog = load_catalog("rust")
    assert catalog is not None, "the rust catalogue must load"
    return {
        p.name: (p.boundary, p.kind)
        for p in catalog.primitives
        if p.module == module
    }


#: Free functions — stable in rustc 1.94.0, receiver shape read from the
#: signature. These are the rows that actually FIRE (function-kind).
_ENV = [
    ("vars_os", "env_read"),
    ("set_var", "env_write"),
    ("remove_var", "env_write"),
    ("set_current_dir", "env_write"),
    ("temp_dir", "host_info_read"),
    ("home_dir", "host_info_read"),
]


class TestEnvGainsItsMutationAndHostReadHalves:
    @pytest.mark.parametrize("name,boundary", _ENV)
    def test_row_present_as_a_function(self, name: str, boundary: str) -> None:
        rows = _rows("std::env")
        assert name in rows, f"std::env::{name} is stable and reaches the OS"
        assert rows[name] == (boundary, "function")

    def test_set_current_dir_keeps_the_promise_the_catalogue_made(self) -> None:
        """rust.yaml said chdir had no row only because rust's spelling was
        unaudited, and named python's env_write as the answer in advance."""
        assert _rows("std::env")["set_current_dir"] == ("env_write", "function")


class TestChildProcessInteractionIsIpc:
    """python: os.kill is ipc_send, os.wait is ipc_recv. rust follows."""

    def test_kill_signals_another_process(self) -> None:
        assert _rows("std::process::Child")["kill"] == ("ipc_send", "method")

    @pytest.mark.parametrize("name", ["wait", "try_wait", "wait_with_output"])
    def test_reaping_a_child_receives_its_status(self, name: str) -> None:
        assert _rows("std::process::Child")[name] == ("ipc_recv", "method")

    @pytest.mark.parametrize("name", ["write", "write_all", "write_fmt"])
    def test_writing_the_childs_stdin_is_ipc_send(self, name: str) -> None:
        assert _rows("std::process::ChildStdin")[name] == ("ipc_send", "method")

    @pytest.mark.parametrize("module", ["std::process::ChildStdout",
                                        "std::process::ChildStderr"])
    @pytest.mark.parametrize("name", ["read", "read_to_end", "read_to_string"])
    def test_reading_the_childs_output_is_ipc_recv(
        self, module: str, name: str,
    ) -> None:
        assert _rows(module)[name] == ("ipc_recv", "method")


class TestStdThreadStaysAbsentEntirely:
    """The judgment this item was filed to make. See the module docstring."""

    @pytest.mark.parametrize("module", [
        "std::thread", "std::thread::Builder", "std::thread::JoinHandle",
        "std::thread::Thread", "std::thread::Scope",
    ])
    def test_no_rows(self, module: str) -> None:
        assert _rows(module) == {}, (
            "a thread lives inside the process, and RUST_MIN_STACK is read "
            "INTERNALLY — its value never reaches the caller. A boundary that "
            "every stdlib-backed call satisfies classifies nothing."
        )


class TestTheRefusalsStayRefused:
    def test_a_pid_is_not_rowed_by_python_precedent(self) -> None:
        """python deliberately holds os.getpid out; the INV-tutar split
        recorded that adding it would be an ADDITION, not a fold-in."""
        assert "id" not in _rows("std::process")
        assert "id" not in _rows("std::process::Child")

    @pytest.mark.parametrize("name", ["env", "env_clear", "env_remove", "envs",
                                      "current_dir", "arg", "args",
                                      "stdin", "stdout", "stderr"])
    def test_command_builder_setters_are_absent(self, name: str) -> None:
        """They mutate a builder in THIS process. The child receives them at
        spawn, and `spawn` is already rowed — the same reason WI-bupor
        excluded OpenOptionsExt / DirBuilderExt setters."""
        assert name not in _rows("std::process::Command")

    @pytest.mark.parametrize("name", ["code", "success", "exit_ok"])
    def test_exit_status_accessors_are_absent(self, name: str) -> None:
        """Reading an already-collected status — the MetadataExt rule."""
        assert name not in _rows("std::process::ExitStatus")

    @pytest.mark.parametrize("name", ["piped", "null", "inherit"])
    def test_stdio_constructors_are_absent(self, name: str) -> None:
        assert name not in _rows("std::process::Stdio")

    @pytest.mark.parametrize("name", ["join_paths", "split_paths"])
    def test_path_string_helpers_are_absent(self, name: str) -> None:
        """Pure OsString manipulation; they touch no environment."""
        assert name not in _rows("std::env")

    def test_available_parallelism_is_absent_by_cpu_count_precedent(self) -> None:
        """python holds os.cpu_count out of its rows for the same reason it
        holds getpid out. Same tension, same answer, recorded not assumed."""
        assert "available_parallelism" not in _rows("std::thread")
