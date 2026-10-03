# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-mujod: a module slot with MORE leading components than the row names a DIFFERENT owner.

``io_boundary._module_matches`` arm 3 ("dropped qualification") accepted a
component-SUFFIX match in EITHER direction. Its own justification covers one:
the edge hint is the unqualified tail of the catalogue's qualified module (go
``http`` for ``net/http``, java ``System`` for ``java.lang.System``). The other
direction -- the hint carries LEADING components the row does not -- is a
more-qualified owner path, which under ADR-0051's axiom (the module key names
an owner path) is a different owner. It classified ``mycsvlib.csv.writer`` as
stdlib ``csv.writer`` and koel's own ``@/services/http`` (a tsconfig path
alias) as node's ``http``.

THE ORDER IS THE ITEM'S: QUALIFY FIRST, THEN TIGHTEN. Three go rows were
reachable from their real import path ONLY through that direction, because
they were spelled under-qualified -- ``filepath`` for ``path/filepath``,
``grpc`` for ``google.golang.org/grpc`` and ``unix`` for
``golang.org/x/sys/unix`` -- and the go analyzer stamps the full import path in
the slot. Respelling them keys each row by its import path (arm 1 matches the
slot exactly), and the bare spelling still reaches it through the justified
catalogue-longer direction. Only then does refusing the hint-longer direction
lose no true match.

THE TAINT RESOLVED-EDGE GATE KEEPS THE DIRECTION, ON PURPOSE. It compares a
catalogue module against a module derived from a FILE PATH
(``packages.hypergumbo-core.src.hypergumbo_core.cli``), where the leading
components are checkout directories, not owner identity. It asks for that
explicitly (``hint_is_path=True``); the module-slot filter
(:func:`io_boundary.named_lookup_arm`, the ONE row-choice rule for io
boundaries AND taint) does not.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hypergumbo_core.cli import _rehydrate_io_boundary_edges
from hypergumbo_core.io_boundary import (
    _module_matches,
    load_catalog,
    tag_io_boundaries,
)

#: The go rows respelled to their import path, written out rather than derived
#: from the catalogue so a catalogue that lost a row cannot agree with this by
#: construction.
_REQUALIFIED: dict[str, tuple[str, frozenset[str]]] = {
    "filepath": ("path/filepath", frozenset({"Walk", "WalkDir", "Glob"})),
    "grpc": ("google.golang.org/grpc",
             frozenset({"Dial", "DialContext", "NewClient"})),
    "unix": ("golang.org/x/sys/unix", frozenset({
        "Open", "Openat", "Read", "Pread", "Readlink", "Stat", "Lstat",
        "Fstat", "Fstatat", "Write", "Pwrite", "Mkdir", "Rmdir", "Unlink",
        "Unlinkat", "Renameat", "Mkdirat", "Accept", "Accept4", "Recvfrom",
        "Recvmsg", "Socket", "Bind", "Listen", "Connect", "Sendto", "Sendmsg",
    })),
}


class TestGoRowsAreKeyedByTheirImportPath:
    """Step 1: no go row depends on the hint-longer direction to be reached."""

    @pytest.mark.parametrize("bare", sorted(_REQUALIFIED))
    def test_every_row_is_spelled_as_its_import_path(self, bare: str) -> None:
        import_path, names = _REQUALIFIED[bare]
        rows = load_catalog("go").primitives
        got = {p.name for p in rows if p.module == import_path}
        assert names <= got, sorted(names - got)
        assert not [p for p in rows if p.module == bare], (
            f"a go row is still spelled {bare!r}, which the go analyzer never "
            f"stamps -- it emits {import_path!r}"
        )

    @pytest.mark.parametrize("bare", sorted(_REQUALIFIED))
    def test_the_import_path_slot_reaches_the_row_exactly(self, bare: str) -> None:
        import_path, names = _REQUALIFIED[bare]
        catalog = load_catalog("go")
        for name in sorted(names):
            row = catalog.lookup_with_module(
                name, import_path, call_construct="function",
            )
            assert row is not None and row.module == import_path, (name, row)

    @pytest.mark.parametrize("bare", sorted(_REQUALIFIED))
    def test_the_bare_spelling_still_reaches_the_row(self, bare: str) -> None:
        """The justified, catalogue-longer direction is untouched."""
        import_path, names = _REQUALIFIED[bare]
        name = sorted(names)[0]
        row = load_catalog("go").lookup_with_module(
            name, bare, call_construct="function",
        )
        assert row is not None and row.module == import_path

    def test_real_go_analyzer_output_classifies_through_the_import_path(
        self, tmp_path: Path,
    ) -> None:
        """The production shape: the analyzer's slot is the full import path."""
        from hypergumbo_lang_mainstream.go import analyze_go

        (tmp_path / "go.mod").write_text("module example.com/fx\n\ngo 1.22\n")
        (tmp_path / "main.go").write_text(
            'package main\n\n'
            'import (\n\t"path/filepath"\n\n'
            '\t"golang.org/x/sys/unix"\n\t"google.golang.org/grpc"\n)\n\n'
            'func main() {\n'
            '\t_ = filepath.Walk(".", nil)\n'
            '\tfd, _ := unix.Socket(1, 2, 0)\n'
            '\t_ = unix.Connect(fd, nil)\n'
            '\t_, _ = grpc.NewClient("x")\n'
            '}\n'
        )
        raw = [e.to_dict() for e in analyze_go(tmp_path).edges]
        edges = _rehydrate_io_boundary_edges(raw)
        tag_io_boundaries(edges, {"go": load_catalog("go")})
        got = {
            e.dst.split(":")[-2]: (e.meta or {}).get("io_primitive")
            for e in edges if e.edge_type == "calls"
        }
        assert {"Walk", "Socket", "Connect", "NewClient"} <= set(got), got  # reach
        assert got["Walk"] == "path/filepath.Walk"
        assert got["Socket"] == "golang.org/x/sys/unix.Socket"
        assert got["Connect"] == "golang.org/x/sys/unix.Connect"
        assert got["NewClient"] == "google.golang.org/grpc.NewClient"
