# SPDX-License-Identifier: AGPL-3.0-or-later
"""The enumerated I/O surface of rust's ``std::io`` (WI-gihos).

THE QUESTION THIS ITEM WAS FILED TO ANSWER was "what does a wrapper like
``BufReader`` inherit from the reader it wraps". Enumerating the module
answered a different and larger one first, so the rows below are shaped by a
STRUCTURAL fact rather than by a taste call about wrappers.

A TRAIT IS NOT A RECEIVER TYPE, so a trait-keyed row can never match.
:func:`~hypergumbo_core.io_boundary.gate_named_entry` filters METHOD-kind hits
out whenever there is no usable module hint, and a module hint can only come
from receiver-type inference. No rust value ever has type ``Read`` — values
have type ``File``, ``TcpStream``, ``BufReader<R>``. So a row on the
``std::io::Read`` module is unmatchable BY CONSTRUCTION, which is the INV-nular
defect committed deliberately rather than by accident.

MEASURED, on the six rust-bearing surveys of the 42-repo cohort: ``std::io``
produces 156 destination edges and NOT ONE of them is a trait-method call.
Every edge whose module slot is ``std::io::Read`` / ``::Write`` / ``::BufRead``
carries the name ``module`` — it is a ``use std::io::Write;`` IMPORT. (Those
import edges are also why the four pre-existing ``std::io::Write`` rows have
never starved their module: ``method_starved_modules`` counts ``calls`` edges
only.) 70 of the 102 bare-``std::io`` edges are ``Error`` / ``ErrorKind`` /
``Error::new`` — pure error handling, no I/O at all.

WHERE THE ROWS GO INSTEAD: THE CONCRETE TYPE. This is not a new invention —
it is the pattern rust.yaml ALREADY uses for ``std::net::TcpStream``, which
carries ``read`` / ``read_to_end`` / ``write`` / ``write_all`` even though
every one of those names comes from a trait; and it is what python.yaml does
with WI-fuvuj's synthetic ``file`` module, which carries ``read`` / ``write``
rather than putting them on ``io.IOBase``. ``std::fs::File`` was the gap: it
implements Read, Write and Seek and carried NONE of their methods, while
``TcpStream`` carried its equivalents — an internal inconsistency in one
catalogue about one question.

THESE ROWS ARE CORRECT AND CURRENTLY INERT, AND THAT IS STATED HERE RATHER
THAN DISCOVERED LATER. Measured across 11,691 rust call edges in the cohort,
the rust catalogue matches ZERO method-kind primitives — every match is
function-kind. The 78 genuine trait-method call edges (46 ``write_all``, 15
``read_to_end``, 10 ``read_to_string``, 6 ``read_line``) all arrive as
``rust:external:0-0:<name>:external_symbol`` with a null ``dst_ref``, and
``gate_named_entry`` rightly refuses a method-kind hit with no module hint.
That is INV-linub L3/L4, so EVERY method row in rust.yaml is dead today,
TcpStream's included: a file read and a socket read are equally invisible.
Adding correct rows does not fix that and is not claimed to — it removes the
inconsistency and makes the catalogue true, so the rows are live the moment
receiver evidence is.

THE WRAPPER ANSWER, AND IT IS MEASURED RATHER THAN REASONED. A wrapper's
boundary is the boundary of what it wraps, which the catalogue cannot know. In
``rage`` — the one repository in the cohort that uses BufReader heavily — 8 of
its ~10 ``BufReader::new`` sites wrap an IN-MEMORY BYTE SLICE
(``TEST_SK.as_bytes()``), not a file. A ``BufReader`` fs_read row would
therefore be MAJORITY-WRONG on the first real repository that exercises it.
The boundary is established at the wrapped thing's own constructor, and
``File::open`` is already rowed. So wrappers get no rows, and this lands
OPPOSITE to what refuse-by-default intuition would have said.

``io::copy`` is refused for the same reason and with the same kind of evidence:
its measured call site in ``rage`` is ``fn write_output<R: io::Read, W:
io::Write>(mut input: R, mut output: W)`` — generic by construction, so the
boundary is unknowable at the call site rather than merely unknown.
"""
from __future__ import annotations

import pytest

from hypergumbo_core.io_boundary import load_catalog
from hypergumbo_core.verify_claims import method_starved_modules


def _rows(module: str) -> dict[str, tuple[str, str]]:
    catalog = load_catalog("rust")
    assert catalog is not None, "the rust catalogue must load"
    return {
        p.name: (p.boundary, p.kind)
        for p in catalog.primitives
        if p.module == module
    }


#: ``std::fs::File`` implements Read, Write and Seek. Every name here is
#: stable in the installed toolchain (rustc 1.94.0) and was read from the
#: trait's own signature in ``library/std/src/io/mod.rs``, not recalled.
_FILE_TRAIT_METHODS = [
    ("read", "fs_read"),
    ("read_vectored", "fs_read"),
    ("read_to_end", "fs_read"),
    ("read_to_string", "fs_read"),
    ("read_exact", "fs_read"),
    ("write", "fs_write"),
    ("write_vectored", "fs_write"),
    ("write_all", "fs_write"),
    ("write_fmt", "fs_write"),
]


class TestFileCarriesTheTraitMethodsItImplements:
    """The gap this item found: a file read and a file write were both blind."""

    @pytest.mark.parametrize("name,boundary", _FILE_TRAIT_METHODS)
    def test_row_present_as_a_method(self, name: str, boundary: str) -> None:
        rows = _rows("std::fs::File")
        assert name in rows, (
            f"std::fs::File.{name} is stable and reaches a syscall; without "
            f"the row an ordinary file {boundary.split('_')[1]} is invisible"
        )
        assert rows[name] == (boundary, "method")

    def test_this_matches_the_pattern_tcpstream_already_uses(self) -> None:
        """The concrete type carries the trait's methods — the existing rule."""
        stream = _rows("std::net::TcpStream")
        assert stream["read"] == ("net_recv", "method")
        assert stream["write_all"] == ("net_send", "method")
        # ...and File must now be symmetric with it.
        f = _rows("std::fs::File")
        assert f["read"] == ("fs_read", "method")
        assert f["write_all"] == ("fs_write", "method")


class TestTheTraitsThemselvesStayUnrowed:
    """A trait is not a receiver type, so a trait row cannot ever match."""

    @pytest.mark.parametrize("trait", ["std::io::Read", "std::io::Seek",
                                       "std::io::BufRead"])
    def test_no_rows_on_the_trait(self, trait: str) -> None:
        assert _rows(trait) == {}, (
            f"{trait} is a TRAIT. gate_named_entry drops method-kind hits "
            "with no module hint, and receiver inference can never produce a "
            "trait as a module hint, so these rows would be unmatchable by "
            "construction (INV-nular). The methods belong on the concrete "
            "types that implement them."
        )


class TestWrappersInheritAnUnknowableBoundary:
    """Measured: 8 of ~10 BufReader::new sites in rage wrap a byte slice."""

    @pytest.mark.parametrize("wrapper", [
        "std::io::BufReader", "std::io::BufWriter", "std::io::LineWriter",
        "std::io::Cursor", "std::io::Take", "std::io::Chain",
    ])
    def test_no_rows_on_the_wrapper(self, wrapper: str) -> None:
        assert _rows(wrapper) == {}, (
            f"{wrapper} takes its boundary from what it wraps, which the "
            "catalogue cannot know; rowing it fs_* would be majority-wrong "
            "on the first real repository that exercises it."
        )


class TestTheRefusalsStayRefused:
    """Each refusal has a reason, and the reason is on the row not in prose."""

    def test_generic_helpers_are_absent(self) -> None:
        """copy / read_to_string take generic readers — unknowable, not unknown."""
        io_rows = _rows("std::io")
        for name in ("copy", "read_to_string"):
            assert name not in io_rows

    def test_null_devices_are_absent(self) -> None:
        """empty / repeat / sink move no data across any boundary."""
        io_rows = _rows("std::io")
        for name in ("empty", "repeat", "sink"):
            assert name not in io_rows

    def test_pipe_is_absent_by_python_precedent(self) -> None:
        """python.yaml rules descriptor PLUMBING out: it moves no data."""
        assert "pipe" not in _rows("std::io")

    def test_seek_family_is_absent_by_the_try_clone_precedent(self) -> None:
        """A syscall, but nothing crosses — the rule that excluded try_clone."""
        f = _rows("std::fs::File")
        for name in ("seek", "rewind", "seek_relative", "stream_position"):
            assert name not in f

    def test_flush_is_absent_from_file_by_python_precedent(self) -> None:
        """python.yaml: flush commits writes ALREADY COUNTED, and a flush row
        made every bare .flush() read as a direct unwrapped fs-write."""
        assert "flush" not in _rows("std::fs::File")

    def test_read_adapters_are_absent(self) -> None:
        """by_ref / bytes / chain / take build an adapter and perform no I/O."""
        f = _rows("std::fs::File")
        for name in ("by_ref", "bytes", "chain", "take"):
            assert name not in f

    def test_unstable_names_are_absent(self) -> None:
        """Unstable in rustc 1.94.0 — a catalogue row must name stable API."""
        f = _rows("std::fs::File")
        for name in ("read_buf", "read_buf_exact", "read_array",
                     "is_read_vectored", "is_write_vectored",
                     "write_all_vectored"):
            assert name not in f

    def test_error_handling_is_not_io(self) -> None:
        """70 of the 102 measured bare-std::io edges are Error / ErrorKind."""
        assert _rows("std::io::Error") == {}
        assert _rows("std::io::ErrorKind") == {}


class TestStdioHandlesCarryTheirReadAndWriteMethods:
    """Concrete types whose entry points (stdin/stdout/stderr) are already rowed."""

    @pytest.mark.parametrize("name", ["read", "read_to_end", "read_to_string",
                                      "read_exact", "read_line", "lines"])
    def test_stdin_reads_are_ipc_recv(self, name: str) -> None:
        assert _rows("std::io::Stdin")[name] == ("ipc_recv", "method")

    @pytest.mark.parametrize("module", ["std::io::Stdout", "std::io::Stderr"])
    @pytest.mark.parametrize("name", ["write", "write_all", "write_fmt"])
    def test_stdout_and_stderr_writes_are_logging(
        self, module: str, name: str,
    ) -> None:
        assert _rows(module)[name] == ("logging", "method")

    @pytest.mark.parametrize("name", ["read", "read_to_end", "read_to_string",
                                      "read_exact", "read_line", "read_until",
                                      "fill_buf"])
    def test_stdin_lock_reads_are_ipc_recv(self, name: str) -> None:
        """`lock` moves no data, so without these a read through
        `stdin().lock()` would be invisible — this item's own gap, repeated."""
        assert _rows("std::io::StdinLock")[name] == ("ipc_recv", "method")

    @pytest.mark.parametrize("module", ["std::io::StdoutLock",
                                        "std::io::StderrLock"])
    @pytest.mark.parametrize("name", ["write", "write_all", "write_fmt"])
    def test_locked_writes_are_logging(self, module: str, name: str) -> None:
        assert _rows(module)[name] == ("logging", "method")

    @pytest.mark.parametrize("module", ["std::io::Stdout", "std::io::Stderr",
                                        "std::io::StdoutLock",
                                        "std::io::StderrLock",
                                        "std::fs::File"])
    def test_flush_is_absent_everywhere_by_python_precedent(
        self, module: str,
    ) -> None:
        assert "flush" not in _rows(module)

    @pytest.mark.parametrize("name", ["read", "read_to_end", "read_to_string",
                                      "read_exact"])
    def test_pipe_reads_are_ipc_recv(self, name: str) -> None:
        """`pipe()` itself is plumbing, but the bytes really do cross."""
        assert _rows("std::io::PipeReader")[name] == ("ipc_recv", "method")

    @pytest.mark.parametrize("name", ["write", "write_all", "write_fmt"])
    def test_pipe_writes_are_ipc_send(self, name: str) -> None:
        assert _rows("std::io::PipeWriter")[name] == ("ipc_send", "method")

    def test_is_terminal_is_absent_and_the_tension_is_recorded(self) -> None:
        """isatty(2) moves no data — the rule that excludes `try_clone`.
        python.yaml rows `os.isatty` as fs_read and so points the other way;
        the catalogue header states that tension rather than hiding it, so
        this refusal is overturnable without re-deriving it."""
        for module in ("std::fs::File", "std::io::Stdin", "std::io::Stdout",
                       "std::io::Stderr", "std::io::IsTerminal"):
            assert "is_terminal" not in _rows(module)

    def test_lock_is_not_io(self) -> None:
        """`.lock()` acquires a mutex on the handle's buffer and moves no data."""
        for module in ("std::io::Stdin", "std::io::Stdout", "std::io::Stderr"):
            assert "lock" not in _rows(module)


class TestAddingTheseRowsDidNotStarveAnyModule:
    """INV-soval's fix is load-bearing underneath every row in this file.

    A module that declares ONLY methods cannot be satisfied by a call whose
    construct is not ``method``, and rust does not stamp ``call_construct`` on
    an associated-function call to an external type. This is the executable
    form of that dependency — it fails if INV-soval is reverted beneath these
    rows, exactly as the ``std::fs`` suite guards WI-bupor's.
    """

    def test_a_file_open_still_satisfies_file(self) -> None:
        catalogs = {"rust": load_catalog("rust")}
        edges = [{
            "src": "rust:src/main.rs:1-9:main:function",
            "dst": "rust:std::fs::File:0-0:open:external_symbol",
            "type": "calls",
            "meta": {"call_construct": "function"},
        }, {
            "src": "rust:src/main.rs:1-9:main:function",
            "dst": "rust:std::collections::HashMap:0-0:insert:external_symbol",
            "type": "calls",
            "meta": {"call_construct": "method"},
        }]
        assert "std::fs::File" not in method_starved_modules(edges, catalogs)

    def test_a_realistic_stdin_read_does_not_starve(self) -> None:
        """The six new stdio modules are METHOD-ONLY, which is the shape that
        makes ``std::path::Path`` starve in 3 of 3 rust repos — ``Path::new``
        is an associated fn, so it lands on the module with no construct and
        nothing can satisfy it. ``Stdin``/``Stdout``/``Stderr`` carry no stable
        associated function for such a call to come from, so the trap has no
        way to spring; this pins that rather than trusting it."""
        catalogs = {"rust": load_catalog("rust")}
        edges = [{
            "src": "rust:src/main.rs:1-9:main:function",
            "dst": "rust:std::io:0-0:stdin:external_symbol",
            "type": "calls",
            "meta": {"call_construct": "function"},
        }, {
            "src": "rust:src/main.rs:1-9:main:function",
            "dst": "rust:std::io::Stdin:0-0:read_line:external_symbol",
            "type": "calls",
            "meta": {"call_construct": "method"},
        }]
        assert method_starved_modules(edges, catalogs) == []

    def test_a_bare_use_import_never_starves_a_module(self) -> None:
        """The reason the pre-existing std::io::Write rows have been inert
        rather than harmful: an import is not a ``calls`` edge."""
        catalogs = {"rust": load_catalog("rust")}
        edges = [{
            "src": "rust:src/main.rs:1-9:main:function",
            "dst": "rust:std::io::Write:0-0:module:external_symbol",
            "type": "imports",
            "meta": {"call_construct": "method"},
        }]
        assert method_starved_modules(edges, catalogs) == []
