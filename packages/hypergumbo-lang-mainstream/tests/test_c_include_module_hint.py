# SPDX-License-Identifier: AGPL-3.0-or-later
"""A bare ``send()`` carries the file's ``#include`` set (WI-lajus).

C has no namespaces, so ``c.yaml`` lists ``send`` / ``recv`` / ``read`` /
``write`` / ``open`` / ``close`` under ``ambiguous_names`` — a bare call could
be libc or a project-local function of the same name — and
``gate_named_entry`` refuses an ambiguous short name outright when there is no
module evidence. THAT GUARD IS CORRECT. The evidence that lifts the ambiguity
is the file's include set, here its ``#include <sys/socket.h>``.

The mechanism is the one ``cpp.py`` uses (WI-rupik / WI-mafik): the
comma-joined ``#include`` set goes into the module slot, and
``_module_hint_candidates`` (INV-funuf) splits that slot on commas and
normalises ``sys/socket.h`` to the ``sys/socket`` spelling ``c.yaml``
declares. C's catalogue is the one cpp *inherits*.

WHEN AN IN-REPO ``send`` WINS. The stamp is applied only on the unresolved
branch, and the resolver is built over repo-wide ``global_symbols``. A
``send`` defined in the CALLING file resolves to it and never reaches the
stamp. A ``send`` defined in ANOTHER file does not capture a call whose
declaring header the caller includes (WI-rimon): it is an interposer or a
platform shim of that same contract, so the call stays the libc one and is
stamped.
"""
from __future__ import annotations

from pathlib import Path

from hypergumbo_core.module_sets import expand_module_slot
from hypergumbo_lang_mainstream.c import analyze_c


def _module_slots(tmp_path: Path, name: str) -> list[str]:
    """Module slot of every unresolved call edge to ``name``."""
    result = analyze_c(tmp_path)
    out = []
    for edge in result.edges:
        if edge.edge_type != "calls":
            continue
        parts = edge.dst.split(":")
        if len(parts) >= 4 and parts[3] == name and parts[-1] == "unresolved":
            out.append(expand_module_slot(parts[1]))
    return out


class TestTheIncludeSetReachesTheModuleSlot:
    def test_a_bare_send_carries_sys_socket(self, tmp_path: Path) -> None:
        (tmp_path / "net.c").write_text(
            "#include <sys/socket.h>\n"
            "#include <unistd.h>\n"
            "int relay(int fd, const char *b, int n) {\n"
            "    return send(fd, b, n, 0);\n"
            "}\n"
        )
        slots = _module_slots(tmp_path, "send")
        assert slots, "no unresolved call edge for send"
        assert all(s != "external" for s in slots), slots
        assert any("sys/socket.h" in s for s in slots), slots

    def test_the_slot_is_the_comma_joined_include_set(
        self, tmp_path: Path
    ) -> None:
        """cpp's contract verbatim: 'could be from any of the included
        headers'. The disjunction is what INV-funuf's expansion consumes."""
        (tmp_path / "net.c").write_text(
            "#include <sys/socket.h>\n"
            "#include <unistd.h>\n"
            "int relay(int fd, char *b, int n) { return recv(fd, b, n, 0); }\n"
        )
        slots = _module_slots(tmp_path, "recv")
        assert slots
        assert "," in slots[0], slots
        assert set(slots[0].split(",")) == {"sys/socket.h", "unistd.h"}

    def test_the_unistd_analogues_are_stamped_too(self, tmp_path: Path) -> None:
        """read/write/close are in ambiguous_names for the same reason
        send/recv are, so they take the same stamp."""
        (tmp_path / "io.c").write_text(
            "#include <unistd.h>\n"
            "int copy(int a, int b, char *buf, int n) {\n"
            "    read(a, buf, n);\n"
            "    write(b, buf, n);\n"
            "    close(a);\n"
            "    return 0;\n"
            "}\n"
        )
        for name in ("read", "write", "close"):
            slots = _module_slots(tmp_path, name)
            assert slots, f"no unresolved edge for {name}"
            assert all(s == "unistd.h" for s in slots), (name, slots)


class TestTheAmbiguityGuardStillHolds:
    def test_a_project_local_send_in_the_calling_file_is_not_stamped(
        self, tmp_path: Path,
    ) -> None:
        """THE GUARD THE STAMP MUST PRESERVE. A file defining its own
        ``send`` calls it, not a sys/socket owner path."""
        (tmp_path / "use.c").write_text(
            "static int send(int fd, const char *b, int n, int f) { return 0; }\n"
            "int go(int fd) { return send(fd, \"x\", 1, 0); }\n"
        )
        assert _module_slots(tmp_path, "send") == [], (
            "a project-local send acquired an external module slot"
        )

    def test_a_same_named_send_in_another_file_does_not_capture_the_libc_call(
        self, tmp_path: Path,
    ) -> None:
        """A same-named definition elsewhere does not capture the call (WI-rimon).

        ``use.c`` includes ``<sys/socket.h>``, so the ``send`` it calls is the
        one that header declares; ``shim.c``'s is a link-time interposer or a
        platform stub (a vendored shim returning ``-1``/``ENOTSUP``, as this
        one returns 0), and name resolution cannot see which the build links.
        Resolving to the stub would hide the socket I/O: a false all-clear.
        """
        (tmp_path / "shim.c").write_text(
            "int send(int fd, const char *b, int n, int f) { return 0; }\n"
        )
        (tmp_path / "use.c").write_text(
            "#include <sys/socket.h>\n"
            "int go(int fd) { return send(fd, \"x\", 1, 0); }\n"
        )
        assert _module_slots(tmp_path, "send") == ["sys/socket.h"]

    def test_a_file_with_no_system_includes_stays_external(
        self, tmp_path: Path
    ) -> None:
        """A zero and a missing input must not look the same: no include set
        means no evidence, which is the ``external`` sentinel, not a guess."""
        (tmp_path / "bare.c").write_text(
            "int go(int fd) { return send(fd, \"x\", 1, 0); }\n"
        )
        assert _module_slots(tmp_path, "send") == ["external"]


class TestPartialEvidenceIsWorseThanNone:
    """Why the stamp is restricted to ``ambiguous_names``.

    A module hint SUPPRESSES the permissive short-name fallback. C reaches most
    of libc through QUOTED project headers, so a slot naming only the system
    includes can be partial evidence, and partial evidence makes the module
    filter refuse a row the fallback would match: stamped unconditionally,
    some real ``stdio`` calls (``fopen``, ``fclose``, ``fprintf``) stop
    matching.
    Names outside ``ambiguous_names`` match by short name and never need the
    hint.
    """

    def test_a_non_ambiguous_name_keeps_the_external_sentinel(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "log.c").write_text(
            '#include <sys/socket.h>\n'
            '#include "project.h"\n'
            "int emit(void) { return fprintf(0, \"x\"); }\n"
        )
        assert _module_slots(tmp_path, "fprintf") == ["external"], (
            "fprintf acquired a partial include hint and lost its fallback"
        )

    def test_ambiguous_and_non_ambiguous_calls_coexist_in_one_file(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "both.c").write_text(
            "#include <unistd.h>\n"
            "#include <stdio.h>\n"
            "int go(int fd, char *b) {\n"
            "    read(fd, b, 1);\n"
            "    fprintf(0, \"x\");\n"
            "    return 0;\n"
            "}\n"
        )
        assert all(s != "external" for s in _module_slots(tmp_path, "read"))
        assert _module_slots(tmp_path, "fprintf") == ["external"]


class TestTheAmbiguousSetComesFromTheCatalogue:
    def test_it_is_read_from_c_yaml_not_restated(self) -> None:
        """One fact, one home: a hand-copied list drifts the day a name is
        added to the catalogue, and nothing errors when it does."""
        from hypergumbo_core.io_boundary import load_catalog

        import hypergumbo_lang_mainstream.c as c_module

        assert c_module._c_ambiguous_names() == frozenset(
            load_catalog("c").ambiguous_names or ()
        )
        assert {"send", "recv", "read", "write"} <= c_module._c_ambiguous_names()
