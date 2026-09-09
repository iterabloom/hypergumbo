# SPDX-License-Identifier: AGPL-3.0-or-later
"""WI-lajus: a bare ``send()`` must carry the file's ``#include`` set.

C has no namespaces, so ``c.yaml`` lists ``send`` / ``recv`` / ``read`` /
``write`` / ``open`` / ``close`` under ``ambiguous_names`` — a bare call could
be libc or a project-local function of the same name — and
``gate_named_entry`` refuses an ambiguous short name outright when there is no
module evidence. THAT GUARD IS CORRECT. What was missing is the evidence that
lifts the ambiguity: the file ``#include <sys/socket.h>`` and the repository
defines no ``send`` of its own.

THE MECHANISM ALREADY EXISTED AND C WAS THE ONLY ONE NOT USING IT. ``cpp.py``
has stamped the comma-joined ``#include`` set into the module slot since
WI-rupik / WI-mafik, and ``_module_hint_candidates`` (INV-funuf) already splits
that slot on commas and normalises ``sys/socket.h`` to the ``sys/socket``
spelling ``c.yaml`` declares. C — whose catalogue cpp *inherits* — emitted
``module_hint="external"`` on every unresolved call. This is a parity gap, not
a new design.

WHY THE IN-REPO GUARD NEEDS NO NEW CODE: the stamp is applied on the
``else`` branch of ``resolver.lookup(...).found``, and the resolver is built
over repo-wide ``global_symbols``. A project that defines its own ``send``
resolves to it and never reaches the stamp — which is the fluent-bit vendored
nuttx shim, the live example the item filed this guard for.
"""
from __future__ import annotations

from pathlib import Path

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
            out.append(parts[1])
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
        """LIVE rule 4: read/write/close are in ambiguous_names for the same
        reason send/recv are, so they are the same defect, not a follow-up."""
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
    def test_a_project_local_send_is_not_stamped(self, tmp_path: Path) -> None:
        """THE GUARD THIS ITEM EXISTS TO PRESERVE. A repo defining its own
        ``send`` must resolve to it, not acquire a sys/socket owner path.
        Loosening a withholding gate is the false-all-clear direction."""
        (tmp_path / "shim.c").write_text(
            "int send(int fd, const char *b, int n, int f) { return 0; }\n"
        )
        (tmp_path / "use.c").write_text(
            "#include <sys/socket.h>\n"
            "int go(int fd) { return send(fd, \"x\", 1, 0); }\n"
        )
        assert _module_slots(tmp_path, "send") == [], (
            "a project-local send acquired an external module slot"
        )

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
    """The regression that stamping unconditionally caused, pinned.

    A module hint SUPPRESSES the permissive short-name fallback. C reaches most
    of libc through QUOTED project headers, so a slot naming only the system
    includes is partial evidence, and partial evidence makes the module filter
    refuse a row that used to match. Measured on qemu-dtc: an unconditional
    stamp moved stdio.fclose 5 -> 2, stdio.fopen 1 -> 0, stdio.fprintf 35 -> 34,
    stdio.fputc 6 -> 5. Names outside ``ambiguous_names`` never needed the hint.
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
