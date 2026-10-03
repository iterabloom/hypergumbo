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


# (catalogue module, edge hint, why) -- the hint carries LEADING components
# the row does not, so it names a different owner.
_HINT_LONGER = [
    ("csv", "mycsvlib.csv", "a third-party package's csv submodule is not stdlib csv"),
    ("http", "@/services/http", "koel's OWN tsconfig path alias, not node's http"),
    ("path", "@tauri-apps/api/path", "tauri's path API, not node's path"),
    ("time", "sys/time", "<sys/time.h> does not declare time(); <time.h> does"),
    ("filepath", "path/filepath", "an under-qualified row is no longer rescued"),
    ("unix", "golang.org/x/sys/unix", "an under-qualified row is no longer rescued"),
]

# (catalogue module, edge hint, why) -- the direction the arm exists for.
_CATALOGUE_LONGER = [
    ("java.lang.System", "System", "java unqualified class reference"),
    ("net/http", "http", "go source spells http.Get after importing net/http"),
    ("golang.org/x/sys/unix", "unix", "the bare identifier go source writes"),
    ("google.golang.org/grpc", "grpc", "the bare identifier go source writes"),
    ("path/filepath", "filepath", "the bare identifier go source writes"),
]


class TestTheModuleSlotRefusesAMoreQualifiedOwner:
    """Step 2: arm 3 accepts the hint-longer direction only for a PATH-derived hint."""

    @pytest.mark.parametrize("catalog,hint,why", _HINT_LONGER)
    def test_a_hint_with_more_leading_components_does_not_match(
        self, catalog: str, hint: str, why: str,
    ) -> None:
        assert _module_matches(catalog, hint) is False, why

    @pytest.mark.parametrize("catalog,hint,why", _CATALOGUE_LONGER)
    def test_the_unqualified_tail_still_matches(
        self, catalog: str, hint: str, why: str,
    ) -> None:
        """Non-vacuity: refusing every suffix would pass the test above."""
        assert _module_matches(catalog, hint) is True, why

    def test_a_path_derived_hint_keeps_the_direction(self) -> None:
        """taint's resolved-edge gate: leading components are DIRECTORIES."""
        path_module = "packages.hypergumbo-core.src.hypergumbo_core.cli"
        assert _module_matches(
            "hypergumbo_core.cli", path_module, hint_is_path=True,
        ) is True
        # ... and the same pair through the module-slot reading is refused,
        # so the keyword is what decides it (mutation control).
        assert _module_matches("hypergumbo_core.cli", path_module) is False

    def test_a_path_derived_hint_still_needs_whole_trailing_components(self) -> None:
        assert _module_matches(
            "log/slog", "pkg.logging", hint_is_path=True,
        ) is False

    @pytest.mark.parametrize("language,name,hint", [
        ("javascript", "get", "@/services/http"),
        ("typescript", "get", "@/services/http"),
        ("python", "dump", "myjsonlib.json"),
    ])
    def test_the_io_row_choice_refuses_it(
        self, language: str, name: str, hint: str,
    ) -> None:
        catalog = load_catalog(language)
        tail = hint.replace("@", "").replace("/", ".").rsplit(".", 1)[-1]
        assert catalog.lookup_with_module(name, tail) is not None, (
            "reach: the row the hint used to reach must exist"
        )
        assert catalog.lookup_with_module(name, hint) is None

    @pytest.mark.parametrize("language,name,hint", [
        ("javascript", "get", "@/services/http"),
        ("python", "dump", "myjsonlib.json"),
    ])
    def test_the_taint_row_choice_refuses_it_too(
        self, language: str, name: str, hint: str,
    ) -> None:
        """INV-foda: ONE row-choice rule, so taint cannot disagree with io."""
        from hypergumbo_core.taint import load_builtin_taint_catalog

        taint = load_builtin_taint_catalog()
        tail = hint.replace("@", "").replace("/", ".").rsplit(".", 1)[-1]
        assert taint.match_sink(language, name, tail) is not None, "reach"
        assert taint.match_sink(language, name, hint) is None

    def test_koel_shape_is_no_longer_node_http(self, tmp_path: Path) -> None:
        """The filed instance, on real analyzer output through the CLI's own path."""
        from hypergumbo_lang_mainstream.js_ts import analyze_javascript

        (tmp_path / "tsconfig.json").write_text(
            '{"compilerOptions": {"paths": {"@/*": ["./js/*"]}}}\n'
        )
        services = tmp_path / "js" / "services"
        services.mkdir(parents=True)
        (services / "http.ts").write_text(
            "export const http = {\n"
            "  get (url: string) { return url },\n"
            "}\n"
        )
        # koel's own spelling: a generic call, ``http.get<User>(...)``,
        # which reaches the pipeline as a module_attr_ref on the alias.
        (services / "authService.ts").write_text(
            "import { http } from '@/services/http'\n"
            "\n"
            "export const authService = {\n"
            "  me: async () => await http.get<User>('me'),\n"
            "}\n"
        )
        raw = [e.to_dict() for e in analyze_javascript(tmp_path).edges]
        edges = _rehydrate_io_boundary_edges(raw)
        reached = [e for e in edges if "@/services/http.get" in e.dst]
        assert reached, sorted(e.dst for e in edges)  # reach
        tag_io_boundaries(edges, {
            "typescript": load_catalog("typescript"),
            "javascript": load_catalog("javascript"),
        })
        assert [(e.meta or {}).get("io_primitive") for e in reached] == (
            [None] * len(reached)
        )
