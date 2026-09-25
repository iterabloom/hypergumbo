# SPDX-License-Identifier: AGPL-3.0-or-later
"""The enumerated I/O surface of rust's filesystem modules (WI-bupor).

WHAT THIS ITEM IS FOR, and why it is not a flag flip. ``module_completeness:
complete`` asserts that EVERY I/O primitive a module exposes is catalogued, so
a module cannot carry it until its TOTAL surface has been enumerated — not the
subset this repository happens to call. ``std::fs`` was already in the
catalogue and therefore never appeared on any "unexamined module" list, while
roughly half its I/O surface was absent: ``File`` carried three associated
functions and NOT ONE of its eleven stable methods, and ``DirBuilder`` /
``DirEntry`` were absent entirely. **Catalogue presence is not catalogue
coverage, and only a total-surface audit can tell them apart.**

WHY THIS COULD NOT SHIP BEFORE INV-soval. ``method_starved_modules`` was
module-granular but tested a per-kind property, so adding a single correct
``std::fs::File`` method row turned that module from function-only into mixed
and made it unsatisfiable by the ``File::open`` call that is the only thing most
repositories do with it. **The correct catalogue content regressed the verdict.**
:class:`TestAddingMethodsDidNotStarveTheModule` is the executable form of that
dependency: it fails if INV-soval's fix is ever reverted underneath these rows.

THE EVIDENCE BAR, inherited from the python stdlib audit and not negotiable:
total surface, EXACT matching (``std::fs`` does not vouch for ``std::fs::File``),
and refuse-by-default, because the wrong answer is a false all-clear. Every row
below was read out of the installed toolchain's ``library/std/src``, with
stability and receiver shape checked per name rather than recalled — the python
audit's unaided first draft got 5 of 32 verdicts wrong and every error was on
the false-all-clear side.

TWO PROBE BUGS WERE FOUND WHILE PRODUCING THIS LIST, both of which returned a
plausible wrong answer rather than an error:

* an impl-tracking regex attributed sixteen module-level functions to
  ``impl DirEntry``; re-derived using INDENTATION as the discriminator.
* a signature regex required ``(`` immediately after the function name, so
  every generic ``pub fn exists<P: AsRef<Path>>(..)`` was silently skipped —
  which is why ``DirBuilder::create`` first appeared to be absent from the
  toolchain. Widening it also corrected that entry's KIND: ``create`` takes
  ``&self`` and is a METHOD, not the associated function it was filed as.
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


#: Every name is stable in the installed toolchain and its receiver shape was
#: read from the signature, not recalled. ``function`` means no receiver exists
#: at the call site, which is load-bearing: a receiverless callee catalogued as
#: a method is unmatchable by construction (INV-nular).
_AUDITED = [
    # -- std::fs free functions: the three gaps in an otherwise complete set.
    ("std::fs", "exists", "fs_read", "function"),
    ("std::fs", "read_link", "fs_read", "function"),
    ("std::fs", "set_permissions", "fs_write", "function"),
    # -- std::fs::File: eleven stable METHODS, none of which were catalogued.
    ("std::fs::File", "metadata", "fs_read", "method"),
    ("std::fs::File", "set_len", "fs_write", "method"),
    ("std::fs::File", "set_permissions", "fs_write", "method"),
    ("std::fs::File", "set_times", "fs_write", "method"),
    ("std::fs::File", "sync_all", "fs_write", "method"),
    ("std::fs::File", "sync_data", "fs_write", "method"),
    ("std::fs::File", "lock", "fs_write", "method"),
    ("std::fs::File", "lock_shared", "fs_write", "method"),
    ("std::fs::File", "try_lock", "fs_write", "method"),
    ("std::fs::File", "try_lock_shared", "fs_write", "method"),
    ("std::fs::File", "unlock", "fs_write", "method"),
    # -- Two types that were absent from the catalogue entirely.
    ("std::fs::DirBuilder", "create", "fs_write", "method"),
    ("std::fs::DirEntry", "metadata", "fs_read", "method"),
    ("std::fs::DirEntry", "file_type", "fs_read", "method"),
    # -- std::path::Path: two stable predicates missing beside eight present.
    ("std::path::Path", "try_exists", "fs_read", "method"),
    ("std::path::Path", "is_symlink", "fs_read", "method"),
    # -- std::path itself had no rows at all.
    ("std::path", "absolute", "fs_read", "function"),
]


class TestTheAuditedSurfaceIsCatalogued:
    @pytest.mark.parametrize(
        "module,name,boundary,kind",
        _AUDITED,
        ids=[f"{m}::{n}" for m, n, _, _ in _AUDITED],
    )
    def test_row_present_with_audited_boundary_and_kind(
        self, module: str, name: str, boundary: str, kind: str
    ) -> None:
        rows = _rows(module)
        assert name in rows, f"{module}::{name} is I/O-bearing and uncatalogued"
        assert rows[name] == (boundary, kind), (
            f"{module}::{name} is catalogued as {rows[name]}, audited as "
            f"({boundary}, {kind}). The KIND is not cosmetic: a receiverless "
            f"callee declared a method can never be matched (INV-nular)."
        )


class TestTheRefusalsStayRefused:
    """The audit's *negative* half, pinned so a later auditor does not "fix" it.

    These names look I/O-shaped and are not. Every one was read and rejected:
    they are in-memory snapshots, builder setters, or lexical path predicates
    that reach no syscall. Cataloguing them would manufacture boundary findings
    that point at nothing.
    """

    @pytest.mark.parametrize(
        "module,name,why",
        [
            ("std::path::Path", "is_absolute",
             "sys::path::is_absolute is path.has_root() -- purely lexical"),
            ("std::path::Path", "is_relative", "delegates to is_absolute"),
            ("std::fs::Metadata", "len", "in-memory snapshot, already stat'd"),
            ("std::fs::Metadata", "modified", "in-memory snapshot"),
            ("std::fs::Metadata", "permissions", "in-memory snapshot"),
            ("std::fs::Permissions", "readonly", "in-memory snapshot"),
            ("std::fs::Permissions", "set_readonly", "in-memory setter"),
            ("std::fs::FileType", "is_dir", "in-memory snapshot"),
            ("std::fs::OpenOptions", "append", "builder setter, only open() does I/O"),
            ("std::fs::OpenOptions", "truncate", "builder setter"),
            ("std::fs::DirEntry", "path", "in-memory, no syscall"),
            ("std::fs::DirEntry", "file_name", "in-memory, no syscall"),
            ("std::fs::File", "options", "returns a builder, no syscall"),
            ("std::fs::File", "try_clone",
             "dup(2) -- a syscall, but nothing crosses and nothing outside the "
             "process changes, so it fails the boundary test that ruled the "
             "mutex out (WI-pavob)"),
        ],
    )
    def test_name_is_not_catalogued(self, module: str, name: str, why: str) -> None:
        assert name not in _rows(module), (
            f"{module}::{name} was catalogued; the audit refused it because "
            f"{why}. Adding it manufactures a boundary finding for a call that "
            f"reaches no syscall."
        )


class TestAddingMethodsDidNotStarveTheModule:
    """THE REASON INV-soval HAD TO LAND FIRST, in executable form.

    Before that fix, ``std::fs::File`` was function-only and starved nothing.
    These rows make it MIXED, and under the old predicate a mixed module could
    not be satisfied by a function-construct call — so a repository that merely
    opens a file would have had its verdict withheld by the act of cataloguing
    ``File``'s methods correctly. If INV-soval is reverted underneath this
    catalogue, this class fails rather than every Rust verdict going quietly
    inconclusive.
    """

    @staticmethod
    def _edges(module: str, name: str) -> list[dict]:
        # One stamped edge so the language is not abstained, plus the call under
        # test with NO call_construct -- which is what the analyzer really emits
        # for an associated-function call to an external type.
        return [
            {"src": "rust:src/main.rs:1-9:m:function",
             "dst": "rust:std::collections::HashMap:0-0:insert:external_symbol",
             "type": "calls", "meta": {"call_construct": "method"}},
            {"src": "rust:src/main.rs:1-9:m:function",
             "dst": f"rust:{module}:0-0:{name}:external_symbol", "type": "calls"},
        ]

    def test_std_fs_File_is_now_mixed_kind(self) -> None:
        kinds = {k for _, k in _rows("std::fs::File").values()}
        assert kinds == {"function", "method"}, (
            f"std::fs::File declares {kinds}; the audit added methods beside "
            f"its associated functions, so it must carry both"
        )

    @pytest.mark.parametrize("name", ["open", "create", "create_new"])
    def test_opening_a_file_still_does_not_starve(self, name: str) -> None:
        assert method_starved_modules(
            self._edges("std::fs::File", name), {"rust": load_catalog("rust")},
        ) == [], (
            f"File::{name} is catalogued as a function and was called as one; "
            f"cataloguing File's METHODS must not withhold the verdict of a "
            f"repo that only opens a file (INV-soval)"
        )

    def test_a_method_call_into_File_also_satisfies(self) -> None:
        edges = self._edges("std::fs::File", "sync_all")
        edges[-1]["meta"] = {"call_construct": "method"}
        assert method_starved_modules(
            edges, {"rust": load_catalog("rust")},
        ) == []

    def test_dirbuilder_is_satisfied_by_the_create_call_that_makes_it_useful(
        self,
    ) -> None:
        """``DirBuilder`` becomes method-only, so the constructor alone starves it.

        That is correct rather than a regression: ``DirBuilder::new()`` performs
        no I/O and is not catalogued, and any code that constructs one goes on
        to call ``.create()``, which is a method and satisfies the module. The
        starving case is a constructor whose result is never used.
        """
        edges = self._edges("std::fs::DirBuilder", "new")
        assert method_starved_modules(
            edges, {"rust": load_catalog("rust")},
        ) == ["std::fs::DirBuilder"]
        edges.append({
            "src": "rust:src/main.rs:1-9:m:function",
            "dst": "rust:std::fs::DirBuilder:0-0:create:external_symbol",
            "type": "calls", "meta": {"call_construct": "method"},
        })
        assert method_starved_modules(
            edges, {"rust": load_catalog("rust")},
        ) == []


#: ``std::os::{unix,windows}::fs``, admitted by owner ruling 2026-08-27 ("in").
#:
#: The catalogue header had excluded ``std::os::*`` as "raw-fd extensions". That
#: noun is correct for what it names — ``AsRawFd`` and friends hand over a
#: descriptor number and reach no syscall — but these modules also carry plain
#: filesystem mutation, and ``std::fs::soft_link`` (the cross-platform way to
#: make a symlink) was already catalogued. So the tool flagged one way of
#: creating a symlink and ignored the other, and a Linux program calling
#: ``chroot`` or ``chown`` through this module read as doing nothing at all.
#:
#: EVERY BOUNDARY BELOW IS FIXED BY CROSS-LANGUAGE PRECEDENT, NOT CHOSEN HERE.
#: python.yaml already rules ``os.chroot``, ``os.chown`` / ``lchown`` /
#: ``fchown``, ``os.symlink`` / ``os.link`` and ``os.pwrite`` as ``fs_write``,
#: and ``os.chdir`` as ``env_write``. A catalogue that disagrees with itself
#: across languages is the defect INV-nular's sweep was built to find.
_OS_AUDITED = [
    ("std::os::unix::fs::FileExt", "read_at", "fs_read", "method"),
    ("std::os::unix::fs::FileExt", "read_exact_at", "fs_read", "method"),
    ("std::os::unix::fs::FileExt", "write_at", "fs_write", "method"),
    ("std::os::unix::fs::FileExt", "write_all_at", "fs_write", "method"),
    ("std::os::unix::fs", "symlink", "fs_write", "function"),
    ("std::os::unix::fs", "chown", "fs_write", "function"),
    ("std::os::unix::fs", "fchown", "fs_write", "function"),
    ("std::os::unix::fs", "lchown", "fs_write", "function"),
    ("std::os::unix::fs", "chroot", "fs_write", "function"),
    ("std::os::windows::fs::FileExt", "seek_read", "fs_read", "method"),
    ("std::os::windows::fs::FileExt", "seek_write", "fs_write", "method"),
    ("std::os::windows::fs", "symlink_file", "fs_write", "function"),
    ("std::os::windows::fs", "symlink_dir", "fs_write", "function"),
]


class TestThePlatformExtensionSurface:
    @pytest.mark.parametrize(
        "module,name,boundary,kind",
        _OS_AUDITED,
        ids=[f"{m}::{n}" for m, n, _, _ in _OS_AUDITED],
    )
    def test_row_present_with_audited_boundary_and_kind(
        self, module: str, name: str, boundary: str, kind: str
    ) -> None:
        rows = _rows(module)
        assert name in rows, f"{module}::{name} reaches a syscall and is uncatalogued"
        assert rows[name] == (boundary, kind), (
            f"{module}::{name} is catalogued as {rows[name]}, audited as "
            f"({boundary}, {kind})"
        )

    @pytest.mark.parametrize(
        "module,name,why",
        [
            # The exclusion's real target: descriptor plumbing, no syscall.
            ("std::os::unix::io::AsRawFd", "as_raw_fd", "hands over a descriptor number"),
            ("std::os::unix::io::FromRawFd", "from_raw_fd", "wraps a descriptor number"),
            # Accessors on an already-fetched stat struct. This is MOST of
            # std::os::unix::fs by method count, which is why the module looks
            # far larger than its I/O surface.
            ("std::os::unix::fs::MetadataExt", "uid", "reads a fetched stat struct"),
            ("std::os::unix::fs::MetadataExt", "size", "reads a fetched stat struct"),
            ("std::os::unix::fs::MetadataExt", "mtime", "reads a fetched stat struct"),
            ("std::os::unix::fs::PermissionsExt", "mode", "in-memory"),
            ("std::os::unix::fs::PermissionsExt", "set_mode", "in-memory setter"),
            ("std::os::unix::fs::FileTypeExt", "is_fifo", "in-memory"),
            ("std::os::unix::fs::DirEntryExt", "ino", "in-memory, readdir already ran"),
            ("std::os::unix::fs::DirBuilderExt", "mode", "builder setter"),
            ("std::os::unix::fs::OpenOptionsExt", "custom_flags", "builder setter"),
            ("std::os::windows::fs::MetadataExt", "file_size", "reads a fetched struct"),
            ("std::os::windows::fs::FileTypeExt", "is_symlink_dir", "in-memory"),
            ("std::os::windows::fs::FileTimesExt", "set_created", "in-memory setter"),
            ("std::os::windows::fs::OpenOptionsExt", "access_mode", "builder setter"),
            # Unstable in the audited toolchain.
            ("std::os::unix::fs", "mkfifo", "UNSTABLE"),
            ("std::os::windows::fs", "junction_point", "UNSTABLE"),
            ("std::os::windows::fs::FileExt", "seek_read_buf", "UNSTABLE"),
            ("std::os::unix::fs::FileExt", "read_buf_at", "UNSTABLE"),
            ("std::os::unix::fs::FileExt", "write_vectored_at", "UNSTABLE"),
        ],
    )
    def test_name_is_not_catalogued(self, module: str, name: str, why: str) -> None:
        assert name not in _rows(module), (
            f"{module}::{name} was catalogued; the audit refused it because {why}"
        )

    def test_the_symlink_asymmetry_that_motivated_the_ruling_is_gone(self) -> None:
        """Three ways to create a symlink; all three must now be visible.

        `std::fs::soft_link` was catalogued while the platform spellings were
        excluded, so a program creating a symlink was flagged or not depending
        purely on which API it reached for.
        """
        assert "soft_link" in _rows("std::fs")
        assert "symlink" in _rows("std::os::unix::fs")
        assert "symlink_file" in _rows("std::os::windows::fs")

    def test_the_free_function_modules_carry_no_methods_so_cannot_starve(self) -> None:
        """``std::os::*::fs`` gets only free functions, so it never enters the
        method-keyed population and cannot withhold a verdict."""
        for module in ("std::os::unix::fs", "std::os::windows::fs"):
            kinds = {k for _, k in _rows(module).values()}
            assert kinds == {"function"}, f"{module} declares {kinds}"

    @pytest.mark.parametrize(
        "module,name",
        [("std::os::unix::fs::FileExt", "read_at"),
         ("std::os::windows::fs::FileExt", "seek_read")],
    )
    def test_a_method_call_into_a_trait_module_satisfies_it(
        self, module: str, name: str
    ) -> None:
        """The trait modules ARE method-only, so they enter the starvation
        population. Every name they carry is a method, so any real call into
        them is a method call and satisfies them."""
        edges = [
            {"src": "rust:src/lib.rs:1-9:f:function",
             "dst": "rust:std::collections::HashMap:0-0:insert:external_symbol",
             "type": "calls", "meta": {"call_construct": "method"}},
            {"src": "rust:src/lib.rs:1-9:f:function",
             "dst": f"rust:{module}:0-0:{name}:external_symbol",
             "type": "calls", "meta": {"call_construct": "method"}},
        ]
        assert method_starved_modules(edges, {"rust": load_catalog("rust")}) == []
